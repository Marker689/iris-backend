#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Stateless tool-use loop.

Called from `send`, `approve_tool`, and `deny_tool`; each invocation
runs one loop iteration by:

  1. Loading conversation history from the DB.
  2. Checking daily token budgets.
  3. Building the tools list from MCP + stripping the injected
     `case_identifier` when the conversation is case-scoped.
  4. Applying redaction to tool_result blocks in history.
  5. Streaming from the provider, emitting socket events, and either:
     - auto-executing read tools inline via `dispatch_tool_call`, or
     - persisting `case_chat_pending_tool_call` rows for writes and
       stopping until the user resolves them.
  6. On `stop_reason=='tool_use'` with only auto-executed reads,
     re-entering the loop with the freshly appended tool_result.

Zero in-memory pause primitives. Tab-close is a no-op; the conversation
resumes tomorrow with the same pending rows.

A run can be steered while it goes (see "Live-run control" in
`app.business.case_chat`): a stop request ends it at the next check —
mid-stream included — and user turns sent meanwhile are queued, then
handed to the model at the next turn boundary instead of waiting for
the run to end.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Iterator

import gevent
import gevent.queue

from app.blueprints.access_controls import (
    ac_fast_check_current_user_has_case_access,
    ac_fast_check_current_user_has_war_room_access,
)
from app.blueprints.iris_user import iris_current_user
from app.blueprints.rest.v2.mcp import classification as tool_classification
from app.blueprints.rest.v2.mcp.classification import (
    UnclassifiedToolError,
)
from app.blueprints.rest.v2.mcp.dispatch import (
    MCPError,
    build_tools_list,
    dispatch_tool_call,
)
from app.blueprints.rest.v2.mcp.registry import TOOL_REGISTRY
from app.business import case_chat as case_chat_biz
from app.iris_engine.llm.client import (
    ChatbotConfig,
    ChatbotDisabledError,
    get_llm_provider,
    load_config,
)
from app.iris_engine.llm.policy import resolve_for_conversation
from app.models.chatbot_policy import ChatbotPolicy
from app.iris_engine.llm.providers.base import (
    ChatMessage,
    Error,
    LLMEvent,
    MessageEnd,
    TextDelta,
    ToolSpec,
    ToolUseEnd,
)
from app.iris_engine.llm.attachments import attachments_expand
from app.iris_engine.llm.attachments import attachments_reference_block
from app.iris_engine.llm.redaction import redact_content_blocks
from app.iris_engine.llm.system_prompt import system_prompt
from app.iris_engine.utils.tracker import track_activity
from app.models.authorization import CaseAccessLevel, WarRoomAccessLevel
from app.models.case_chat import (
    ROLE_ASSISTANT,
    ROLE_TOOL,
    ROLE_USER,
    CaseChatConversation,
)
from app.models.errors import BusinessProcessingError, ObjectNotFoundError


logger = logging.getLogger('iris.chatbot.loop')


# ---- Event emitter --------------------------------------------------

@dataclass(frozen=True)
class ChatEvent:
    """One outbound event from the loop to the client. Kept protocol-
    agnostic so the namespace layer can convert to SocketIO emits (or,
    if we ever add SSE, translate here)."""
    name: str
    payload: dict[str, Any]


Emitter = Callable[[ChatEvent], None]


# ---- Public entrypoint ---------------------------------------------

def run_one_iteration(
    *,
    conversation: CaseChatConversation,
    emit: Emitter,
    run_token: str | None = None,
) -> None:
    """Run one LLM turn. May persist several messages (assistant text,
    zero-or-more auto-executed tool_results). Stops the moment a write
    tool_use is emitted — the client resolves it and re-invokes this
    function via `approve_tool` / `deny_tool`.

    The loop is a straight-line generator consumer under gevent —
    `for event in provider.stream_completion(...)` yields cooperatively
    between tokens, so a long stream never blocks the worker.

    `run_token` is the run the caller claimed for the conversation;
    without one, the run is claimed here. Either way it is released on
    the way out.
    """
    if run_token is None:
        run_token = case_chat_biz.start_run(conversation.id)
    try:
        _run(conversation=conversation, emit=emit,
             run=_RunControl(conversation.id, run_token))
    finally:
        try:
            case_chat_biz.release_run(conversation.id, run_token)
        except Exception:
            logger.exception('chatbot: failed to release run of conversation %s',
                             conversation.id)


def absorb_user_texts(
    *,
    conversation: CaseChatConversation,
    texts: list[str],
    emit: Emitter,
) -> None:
    """Hand user turns to the model as one user message.

    A user writing while writes wait for approval has moved on: those
    writes are denied first, so every tool_use keeps its tool_result and
    the model reads the new turn knowing nothing was changed.
    """
    user = iris_current_user._get_current_object()  # type: ignore[attr-defined]
    for pending_id in case_chat_biz.list_pending_tool_call_ids(conversation.id):
        try:
            row, transitioned = case_chat_biz.resolve_pending_tool_call(
                pending_id=pending_id, user=user, approve=False,
            )
        except (ObjectNotFoundError, BusinessProcessingError):
            continue
        if not transitioned:
            continue
        case_chat_biz.append_message(
            conversation=conversation,
            role=ROLE_TOOL,
            content=[{
                'type': 'tool_result',
                'tool_use_id': row.tool_use_id,
                'content': _SUPERSEDED_BY_USER,
            }],
            tool_use_id=row.tool_use_id,
        )
        emit(ChatEvent('assistant_tool_result', {
            'tool_use_id': row.tool_use_id,
            'tool_name': row.tool_name,
            'result': {'denied': True, 'message': _SUPERSEDED_BY_USER},
        }))
    # Files sent with these turns ride along with them.
    attachments = case_chat_biz.take_staged_attachments(conversation.id)
    text = '\n\n'.join(t for t in texts if t)
    content: list[dict[str, Any]] = []
    if text or not attachments:
        content.append({'type': 'text', 'text': text})
    content.extend(attachments_reference_block(a) for a in attachments)
    message = case_chat_biz.append_message(
        conversation=conversation,
        role=ROLE_USER,
        content=content,
    )
    case_chat_biz.link_attachments(attachments, message)
    emit(ChatEvent('user_message_persisted', {'message_id': message.id}))


_SUPERSEDED_BY_USER = ('Not run: the user did not approve this action and '
                       'sent a new message instead.')
_NOT_RUN_STOPPED = 'Not run: the user stopped the assistant before this tool ran.'

# How often a run looks for a stop request or queued user turns, at most.
_POLL_INTERVAL_SECONDS = 1.0


class _RunControl:
    """The loop's handle on its live run (see `app.business.case_chat`).

    `check` doubles as the heartbeat, rate-limited so the token stream
    doesn't turn into a query stream. Once it reports a stop, it sticks
    until `resume`.
    """

    def __init__(self, conversation_id: int, run_token: str):
        self.conversation_id = conversation_id
        self.run_token = run_token
        self.interrupted = False
        self._last_check: float | None = None

    def check(self, force: bool = False) -> bool:
        """True when the run should stop where it is."""
        if self.interrupted:
            return True
        now = time.monotonic()
        if (not force and self._last_check is not None
                and now - self._last_check < _POLL_INTERVAL_SECONDS):
            return False
        self._last_check = now
        self.interrupted = case_chat_biz.beat_run(
            self.conversation_id, self.run_token)
        return self.interrupted

    def take_queued_texts(self) -> list[str]:
        return case_chat_biz.take_queued_texts(
            self.conversation_id, self.run_token)

    def finish(self) -> list[str]:
        """End the run; returns user turns queued meanwhile, which keep it going."""
        texts = case_chat_biz.finish_run(self.conversation_id, self.run_token)
        if texts:
            self.interrupted = False
        return texts


def _run(
    *,
    conversation: CaseChatConversation,
    emit: Emitter,
    run: _RunControl,
) -> None:
    # Re-resolve the policy on every send() so a war-room case attach
    # that raised the ceiling since the conversation was created is
    # caught HERE — the loop refuses to run against a weaker provider
    # than the current scope requires. The war-room hook also archives
    # the conversation up front, so this is a belt-and-braces check.
    live = resolve_for_conversation(conversation)
    stamped_level = int(conversation.resolved_restriction_level or 0)
    if live.restriction_level > stamped_level:
        # Something changed — a case was attached whose customer has a
        # stricter policy than this conversation. Archive and tell the
        # user to open a new chat under the new ceiling.
        case_chat_biz.archive_conversation(conversation)
        emit(ChatEvent('error', {
            'message': (
                'This conversation\'s customer policy has become '
                'stricter since it was started. It has been archived — '
                'please open a new chat to continue under the updated '
                'policy.'
            ),
        }))
        return

    # Load the stamped policy, if any, and hand it to load_config.
    stamped_policy: ChatbotPolicy | None = None
    if conversation.resolved_policy_id is not None:
        stamped_policy = (
            ChatbotPolicy.query
            .filter_by(id=conversation.resolved_policy_id)
            .first()
        )

    try:
        cfg = load_config(policy=stamped_policy)
    except Exception as exc:
        logger.exception('failed to load chatbot config')
        emit(ChatEvent('error', {
            'message': f'Chatbot configuration error: {exc}',
        }))
        return

    if not cfg.enabled:
        emit(ChatEvent('error', {
            'message': 'Chatbot is disabled on this server.',
        }))
        return

    user = iris_current_user._get_current_object()  # type: ignore[attr-defined]

    # Budget check — refuse before spending an LLM token.
    if cfg.daily_token_budget_per_user > 0:
        spent_user = case_chat_biz.sum_tokens_today(user_id=user.id)
        if spent_user >= cfg.daily_token_budget_per_user:
            emit(ChatEvent('error', {
                'message': (
                    f'Daily per-user chatbot budget exhausted '
                    f'({spent_user}/{cfg.daily_token_budget_per_user} tokens). '
                    'Try again tomorrow or ask an admin to raise the cap.'
                ),
            }))
            return
    if cfg.daily_token_budget_org > 0:
        spent_org = case_chat_biz.sum_tokens_today()
        if spent_org >= cfg.daily_token_budget_org:
            emit(ChatEvent('error', {
                'message': (
                    f'Daily org-wide chatbot budget exhausted '
                    f'({spent_org}/{cfg.daily_token_budget_org} tokens).'
                ),
            }))
            return

    try:
        provider = get_llm_provider(cfg)
    except ChatbotDisabledError as exc:
        emit(ChatEvent('error', {'message': str(exc)}))
        return

    # Iterate until we either hit stop_reason=='end_turn' (done), a
    # write tool_use pauses us, or the user stops us. Auto-executed reads
    # loop back here without a per-iteration user event. User turns
    # queued meanwhile join at the next turn boundary — and, at the end
    # of the run, keep it going rather than wait for another send.
    max_iterations = max(1, cfg.max_tool_calls_per_turn)
    turns = 0
    while True:
        if run.check(force=True):
            outcome = _TurnResult(interrupted=True)
        else:
            queued = run.take_queued_texts()
            if queued:
                absorb_user_texts(conversation=conversation, texts=queued, emit=emit)
                turns = 0
            if turns >= max_iterations:
                _emit_end(conversation, emit, stop_reason='max_tool_calls')
                outcome = _TurnResult(terminated=True)
            else:
                turns += 1
                outcome = _run_turn(conversation=conversation, cfg=cfg,
                                    provider=provider, user=user, run=run, emit=emit)
        if outcome.errored:
            return
        if not (outcome.terminated or outcome.paused_on_write or outcome.interrupted):
            # All-reads turn → loop back and let the model see the results.
            continue
        if outcome.interrupted:
            _emit_end(conversation, emit, stop_reason='interrupted')
        queued = run.finish()
        if not queued:
            return
        absorb_user_texts(conversation=conversation, texts=queued, emit=emit)
        turns = 0


def _emit_end(
    conversation: CaseChatConversation, emit: Emitter, *,
    stop_reason: str, message_id: int | None = None,
) -> None:
    # Attach live usage so the panel's footer refreshes on every turn
    # without a REST round-trip. `get_conversation_usage` is one SUM
    # query over egress rows scoped to this conversation — cheap.
    emit(ChatEvent('assistant_end', {
        'message_id': message_id,
        'stop_reason': stop_reason,
        'usage': case_chat_biz.get_conversation_usage(
            conversation.id, user_id=conversation.user_id),
    }))


def _run_turn(
    *,
    conversation: CaseChatConversation,
    cfg: ChatbotConfig,
    provider,
    user,
    run: _RunControl,
    emit: Emitter,
) -> _TurnResult:
    """One provider call, and the tool calls it asks for."""
    history = _load_history(conversation, cfg)
    read_only_scope = _scope_is_read_only(conversation)
    tools = _prepare_tools(conversation, read_only_scope=read_only_scope)
    system = system_prompt(
        case_id=conversation.case_id,
        war_room_id=conversation.war_room_id,
        read_only_scope=read_only_scope,
    )

    request_bytes = _estimate_request_bytes(system, history, tools)
    # Redaction toggles are checked inside `_load_history`, so the
    # `redacted` bit here reflects what was actually scrubbed.
    redacted = _history_was_redacted(history, cfg)

    audit_row = case_chat_biz.record_egress(
        conversation=conversation,
        user=user,
        provider=provider.name,
        model=cfg.model,
        request_bytes=request_bytes,
        redacted=redacted,
        request_snapshot=_build_request_snapshot(system, history, tools),
    )

    return _consume_stream(
        provider=provider,
        model=cfg.model,
        system=system,
        history=history,
        tools=tools,
        conversation=conversation,
        audit_row=audit_row,
        run=run,
        emit=emit,
    )


# ---- Internals -----------------------------------------------------

def _tool_result_content(result: Any) -> Any:
    """Coerce a `dispatch_tool_call` return into a shape Anthropic
    accepts for `tool_result.content`.

    Anthropic requires either a string or a list of content blocks
    (`[{type: 'text', text: '...'}, ...]`). Our MCP dispatch returns
    Python dicts / lists / primitives, so we JSON-encode anything
    non-string. Kept as a helper so approve_tool.py (write dispatch)
    and this file (read dispatch) share one funnel — the shape has to
    be identical for the provider adapter to accept it.
    """
    if isinstance(result, str):
        return result
    try:
        return json.dumps(result, default=str)
    except (TypeError, ValueError):
        return str(result)


def _append_not_run(
    conversation: CaseChatConversation, tool_use: dict[str, Any],
) -> None:
    """Answer a tool_use the user stopped before it ran — every tool_use
    needs its tool_result."""
    case_chat_biz.append_message(
        conversation=conversation,
        role=ROLE_TOOL,
        content=[{
            'type': 'tool_result',
            'tool_use_id': tool_use['id'],
            'content': _NOT_RUN_STOPPED,
        }],
        tool_use_id=tool_use['id'],
    )


@dataclass
class _TurnResult:
    terminated: bool = False       # end_turn / stop — done for now
    paused_on_write: bool = False  # write tool_use written to DB; wait for approve/deny
    interrupted: bool = False      # the user stopped the run
    errored: bool = False          # error emitted; the run ends as is


def _load_history(
    conversation: CaseChatConversation, cfg: ChatbotConfig,
) -> list[ChatMessage]:
    """Load messages + apply the max-turns trim + apply redaction to
    role='tool' blocks. Returns fully-materialised `ChatMessage`
    dataclasses ready to hand to a provider adapter.

    Self-heals orphan `tool_use` blocks. If dispatch crashed mid-turn
    in a prior session and the `tool_result` was never persisted, the
    next call to the provider would fail with "tool_use ids were found
    without tool_result blocks" and the whole conversation would be
    stuck. We stitch in a synthetic error `tool_result` for any orphan
    so the model can move on.
    """
    rows = case_chat_biz.list_messages(conversation.id)
    # Trim from the tail — keep the most recent N user turns plus their
    # associated assistant / tool exchanges. Rough heuristic: keep
    # `2 * max_turns` messages (user + assistant pair per turn) plus
    # any interleaved tool messages.
    keep = max(2, cfg.max_turns_per_conversation) * 4
    trimmed = rows[-keep:]
    attachments = case_chat_biz.get_attachments_by_id({
        b['attachment_id']
        for row in trimmed if row.role == ROLE_USER
        for b in (row.content or [])
        if isinstance(b, dict) and b.get('type') == 'attachment'
        and isinstance(b.get('attachment_id'), int)
    })
    out: list[ChatMessage] = []
    for row in trimmed:
        content: list[dict[str, Any]] = row.content or []
        if row.role == ROLE_USER:
            content = attachments_expand(content, attachments)
        if row.role == ROLE_TOOL:
            content, _ = redact_content_blocks(
                content,
                redact_ips=cfg.redact_ips,
                redact_emails=cfg.redact_emails,
                redact_hashes=cfg.redact_hashes,
            )
        out.append(ChatMessage(role=row.role, content=content))
    return _stitch_orphan_tool_uses(out)


def _stitch_orphan_tool_uses(
    history: list[ChatMessage],
) -> list[ChatMessage]:
    """Insert a synthetic `tool_result` after any assistant `tool_use`
    whose id isn't matched by a subsequent tool_result.

    Anthropic's API requires strict `tool_use → tool_result` pairing
    across message boundaries. A crash during dispatch — most notoriously
    the datetime-serialize bug in the emit path — could persist the
    assistant's `tool_use` without a matching `tool_result`, and every
    subsequent send() would fail with a 400 from Anthropic and the
    analyst couldn't recover without archiving. This heals it in-place
    on load.
    """
    # Walk once to collect the set of tool_result ids already present.
    have_result: set[str] = set()
    for msg in history:
        if msg.role != ROLE_TOOL:
            continue
        for b in msg.content or []:
            if isinstance(b, dict) and b.get('type') == 'tool_result':
                tu_id = b.get('tool_use_id')
                if isinstance(tu_id, str):
                    have_result.add(tu_id)

    # Now walk assistant messages and, for each tool_use with no
    # corresponding tool_result, splice a synthetic one in AFTER the
    # assistant message.
    stitched: list[ChatMessage] = []
    for msg in history:
        stitched.append(msg)
        if msg.role != ROLE_ASSISTANT:
            continue
        orphans: list[dict[str, Any]] = []
        for b in msg.content or []:
            if not (isinstance(b, dict) and b.get('type') == 'tool_use'):
                continue
            tu_id = b.get('id')
            if isinstance(tu_id, str) and tu_id not in have_result:
                orphans.append({
                    'type': 'tool_result',
                    'tool_use_id': tu_id,
                    'content': (
                        'Tool call did not complete (internal error). '
                        'Please try again or take a different approach.'
                    ),
                    'is_error': True,
                })
                have_result.add(tu_id)  # dedup if referenced twice
        if orphans:
            logger.warning(
                'chatbot: stitched %d orphan tool_use(s) in conversation history — '
                'a prior dispatch crashed without persisting tool_result',
                len(orphans),
            )
            stitched.append(ChatMessage(role=ROLE_TOOL, content=orphans))
    return stitched


def _history_was_redacted(
    history: list[ChatMessage], cfg: ChatbotConfig,
) -> bool:
    """Second pass over the history to record whether redaction ran —
    cheaper than threading a bool out of `_load_history` and lets the
    audit-row be finalised even when we short-circuited history load.
    """
    if not (cfg.redact_ips or cfg.redact_emails or cfg.redact_hashes):
        return False
    for msg in history:
        if msg.role != ROLE_TOOL:
            continue
        _, changed = redact_content_blocks(
            msg.content,
            redact_ips=cfg.redact_ips,
            redact_emails=cfg.redact_emails,
            redact_hashes=cfg.redact_hashes,
        )
        if changed:
            return True
    return False


def _scope_is_read_only(conversation: CaseChatConversation) -> bool:
    """True when the caller only holds read access on the conversation's scope.

    Dispatch refuses the write anyway, so this is about not dangling
    actions the analyst can't take: the write tools are dropped from the
    list and the system prompt says why.
    """
    if conversation.case_id is not None and not \
            ac_fast_check_current_user_has_case_access(
                conversation.case_id, [CaseAccessLevel.full_access]):
        return True
    if conversation.war_room_id is not None and not \
            ac_fast_check_current_user_has_war_room_access(
                conversation.war_room_id, [WarRoomAccessLevel.full_access]):
        return True
    return False


def _prepare_tools(
    conversation: CaseChatConversation, read_only_scope: bool = False,
) -> list[ToolSpec]:
    """Build the tool list the LLM sees.

    Strips `case_identifier` from case-scoped tools' input_schema when
    the conversation itself is case-scoped, and `war_room_id` when
    war-room-scoped — the LLM shouldn't be asked to guess or supply
    them, and dispatch will override anyway.

    When `read_only_scope`, tools that would mutate the scoped entity are
    dropped entirely: the loop pins every scoped call to the
    conversation's case / war room, so those calls could only ever come
    back access-denied.
    """
    raw = build_tools_list()
    is_case_scoped = conversation.case_id is not None
    is_war_room_scoped = conversation.war_room_id is not None
    strip_keys: list[str] = []
    if is_case_scoped:
        strip_keys.append('case_identifier')
    if is_war_room_scoped:
        strip_keys.append('war_room_id')
    out: list[ToolSpec] = []
    for entry in raw:
        if read_only_scope and _mutates_the_scope(
                entry['name'], is_case_scoped, is_war_room_scoped):
            continue
        schema = dict(entry.get('inputSchema') or {})
        if strip_keys:
            props = dict(schema.get('properties') or {})
            for key in strip_keys:
                props.pop(key, None)
            schema['properties'] = props
            required = [r for r in (schema.get('required') or [])
                        if r not in strip_keys]
            if required:
                schema['required'] = required
            elif 'required' in schema:
                schema.pop('required')
        out.append(ToolSpec(
            name=entry['name'],
            description=entry.get('description') or '',
            input_schema=schema,
        ))
    return out


def _mutates_the_scope(
    tool_name: str, is_case_scoped: bool, is_war_room_scoped: bool,
) -> bool:
    """True when the tool would write to the conversation's own scope.

    Write tools that target something else (creating a case, updating an
    alert) stay available — they're gated on their own permissions, not
    on this case's access level.
    """
    spec = TOOL_REGISTRY.get(tool_name)
    if spec is None or not tool_classification.mutates(tool_name):
        return False
    return ((spec.case_scoped and is_case_scoped)
            or (spec.war_room_scoped and is_war_room_scoped))


def _estimate_request_bytes(
    system: str, history: list[ChatMessage], tools: list[ToolSpec],
) -> int:
    """Rough char-count so audit rows always have a request-size figure
    even when the provider's SDK doesn't expose it. Fast; the DPO view
    is byte-level anyway."""
    n = len(system)
    for m in history:
        n += 8 + sum(len(json.dumps(b, default=str)) for b in (m.content or []))
    for t in tools:
        n += len(t.name) + len(t.description or '')
        n += len(json.dumps(t.input_schema, default=str))
    return n


def _consume_stream(
    *,
    provider,
    model: str,
    system: str,
    history: list[ChatMessage],
    tools: list[ToolSpec],
    conversation: CaseChatConversation,
    audit_row,
    run: _RunControl,
    emit: Emitter,
) -> _TurnResult:
    """Iterate one provider stream. On write tool_use, persist pending
    row(s) and return early with paused_on_write=True. On end_turn,
    persist the final assistant message and return terminated=True.
    On a stop request, keep the text streamed so far and drop the
    tool calls the model hadn't finished asking for."""

    text_buffer: list[str] = []
    tool_uses: list[dict[str, Any]] = []

    events = _interruptible_events(
        provider.stream_completion(
            model=model, system=system, messages=history, tools=tools,
        ),
        run,
    )
    try:
        return _drain_stream(
            events=events, text_buffer=text_buffer, tool_uses=tool_uses,
            conversation=conversation, audit_row=audit_row, run=run, emit=emit,
        )
    finally:
        events.close()


def _drain_stream(
    *,
    events: Iterator[LLMEvent],
    text_buffer: list[str],
    tool_uses: list[dict[str, Any]],
    conversation: CaseChatConversation,
    audit_row,
    run: _RunControl,
    emit: Emitter,
) -> _TurnResult:
    response_bytes = 0
    for event in events:
        response_bytes += _event_bytes(event)
        if isinstance(event, TextDelta):
            text_buffer.append(event.text)
            emit(ChatEvent('assistant_delta', {'text': event.text}))
            continue
        if isinstance(event, ToolUseEnd):
            tool_uses.append({
                'id': event.tool_use_id,
                'name': event.name,
                'input': event.arguments or {},
            })
            continue
        if isinstance(event, MessageEnd):
            # Persist the final assistant message + resolve tool uses.
            case_chat_biz.finalise_egress(
                audit_row=audit_row,
                response_bytes=response_bytes,
                prompt_tokens=event.prompt_tokens,
                completion_tokens=event.completion_tokens,
                cache_read_tokens=event.cache_read_tokens,
                cache_creation_tokens=event.cache_creation_tokens,
            )
            return _finalise_turn(
                text=''.join(text_buffer),
                tool_uses=tool_uses,
                stop_reason=event.stop_reason,
                conversation=conversation,
                run=run,
                emit=emit,
            )
        if isinstance(event, Error):
            case_chat_biz.finalise_egress(
                audit_row=audit_row,
                response_bytes=response_bytes,
                prompt_tokens=None, completion_tokens=None,
            )
            emit(ChatEvent('error', {'message': event.message}))
            return _TurnResult(errored=True)
        # ToolUseStart / ToolUseDelta — no-ops in the loop; the SPA
        # doesn't need them for reads because the final result renders
        # atomically once dispatched, and for writes the pending-row
        # notification carries the args anyway. Could surface later.

    case_chat_biz.finalise_egress(
        audit_row=audit_row,
        response_bytes=response_bytes,
        prompt_tokens=None, completion_tokens=None,
    )
    if run.interrupted:
        # Tool calls that were streamed but never ran would each need a
        # tool_result; the model loses nothing by not seeing them.
        text = ''.join(text_buffer)
        if text.strip():
            case_chat_biz.append_message(
                conversation=conversation,
                role=ROLE_ASSISTANT,
                content=[{'type': 'text', 'text': text}],
            )
        return _TurnResult(interrupted=True)

    # Stream ended without a MessageEnd — treat as error but persist
    # whatever text we captured so the analyst can see partial output.
    return _finalise_turn(
        text=''.join(text_buffer),
        tool_uses=tool_uses,
        stop_reason='end_turn',
        conversation=conversation,
        run=run,
        emit=emit,
    )


_STREAM_DONE = object()


def _interruptible_events(
    events: Iterable[LLMEvent], run: _RunControl,
) -> Iterator[LLMEvent]:
    """Relay a provider stream, ending it early on a stop request.

    The stream is drained in a side greenlet so the run keeps checking
    for a stop while the provider is silent — a model thinking for a
    minute before its first token can be stopped like one mid-sentence.
    Ending early kills that greenlet, which closes the provider's
    response. Provider exceptions are re-raised here.
    """
    relay: gevent.queue.Queue = gevent.queue.Queue()

    def _pump() -> None:
        try:
            for event in events:
                relay.put((event, None))
        except Exception as exc:
            relay.put((_STREAM_DONE, exc))
        else:
            relay.put((_STREAM_DONE, None))

    pump = gevent.spawn(_pump)
    try:
        while not run.check():
            try:
                event, exc = relay.get(timeout=_POLL_INTERVAL_SECONDS)
            except gevent.queue.Empty:
                continue
            if event is _STREAM_DONE:
                if exc is not None:
                    raise exc
                return
            yield event
    finally:
        pump.kill(block=False)


def _finalise_turn(
    *,
    text: str,
    tool_uses: list[dict[str, Any]],
    stop_reason: str,
    conversation: CaseChatConversation,
    run: _RunControl,
    emit: Emitter,
) -> _TurnResult:
    """Persist the assistant message + process any tool_use blocks.

    A stop request is honoured between tool calls: the ones not run by
    then get a tool_result saying so, and nothing new waits for approval.
    """
    content: list[dict[str, Any]] = []
    if text:
        content.append({'type': 'text', 'text': text})
    for tu in tool_uses:
        content.append({
            'type': 'tool_use',
            'id': tu['id'],
            'name': tu['name'],
            'input': tu['input'],
        })
    if not content:
        # Empty assistant turn — unusual but persist an empty text block
        # so the message history stays a valid Anthropic-shaped sequence.
        content.append({'type': 'text', 'text': ''})

    assistant_msg = case_chat_biz.append_message(
        conversation=conversation,
        role=ROLE_ASSISTANT,
        content=content,
    )

    if not tool_uses:
        _emit_end(conversation, emit, stop_reason=stop_reason,
                  message_id=assistant_msg.id)
        return _TurnResult(terminated=True)

    # Classify each tool_use.
    reads_to_execute: list[dict[str, Any]] = []
    writes_to_pend: list[dict[str, Any]] = []
    for tu in tool_uses:
        name = tu['name']
        if tool_classification.is_read_only(name):
            reads_to_execute.append(tu)
        elif tool_classification.is_write(name):
            writes_to_pend.append(tu)
        else:
            # Unclassified — refuse per the safety contract.
            emit(ChatEvent('error', {
                'message': (
                    f'Tool {name!r} is not classified as read or write. '
                    'Contact your administrator — this is a code-level '
                    'gap that must be resolved before the tool can run.'
                ),
            }))
            return _TurnResult(errored=True)

    # Auto-execute reads if allowed. Loads config through the
    # conversation's stamped policy so per-customer overrides for
    # `auto_execute_read_tools` / `auto_approve_write_tools` bind here
    # too — otherwise a strict-local customer would silently fall back
    # to the global auto-approve toggle.
    from app.iris_engine.llm.client import load_config
    stamped = None
    if conversation.resolved_policy_id is not None:
        stamped = (
            ChatbotPolicy.query
            .filter_by(id=conversation.resolved_policy_id)
            .first()
        )
    cfg = load_config(policy=stamped)

    for tu in reads_to_execute:
        if not cfg.auto_execute_read_tools:
            # Admin opted for confirm-all — treat reads as writes.
            writes_to_pend.append(tu)
            continue
        if run.check(force=True):
            _append_not_run(conversation, tu)
            continue
        result = _dispatch_and_track(
            conversation=conversation,
            tool_use=tu,
            emit=emit,
        )
        # Persist tool_result even on failure so the model sees the
        # error and can react — better UX than silently ignoring.
        # Anthropic requires `content` to be a string or list of
        # content blocks; a raw dict from `dispatch_tool_call` fails
        # validation. Serialize to a JSON string.
        case_chat_biz.append_message(
            conversation=conversation,
            role=ROLE_TOOL,
            content=[{
                'type': 'tool_result',
                'tool_use_id': tu['id'],
                'content': _tool_result_content(result),
            }],
            tool_use_id=tu['id'],
        )

    # Full-throttle mode: admin opted to skip the Approve/Deny card
    # for writes. Dispatch each write immediately through the same path
    # reads use (scope override, emit start/result, audit-log). The
    # `pending` flag on the event stays False so the SPA renders the
    # write as a live tool card, not an Approve card.
    if run.check(force=True):
        for tu in writes_to_pend:
            _append_not_run(conversation, tu)
        return _TurnResult(interrupted=True)

    if writes_to_pend and cfg.auto_approve_write_tools:
        for tu in writes_to_pend:
            if run.check(force=True):
                _append_not_run(conversation, tu)
                continue
            result = _dispatch_and_track(
                conversation=conversation,
                tool_use=tu,
                emit=emit,
            )
            case_chat_biz.append_message(
                conversation=conversation,
                role=ROLE_TOOL,
                content=[{
                    'type': 'tool_result',
                    'tool_use_id': tu['id'],
                    'content': _tool_result_content(result),
                }],
                tool_use_id=tu['id'],
            )
        # Fall through — treat this like an all-reads turn so the caller
        # re-enters the loop with the fresh tool_result messages.
        return _TurnResult(interrupted=run.interrupted)

    # Pending writes: persist rows, emit assistant_tool_start events.
    # Apply the scope override at persist time so a tab-close-and-
    # resume-tomorrow flow doesn't lose the case scope, and so the
    # frontend Approve card has a `case_identifier` to render in the
    # diff-of-state description. `_dispatch_and_track` re-asserts this
    # on approve as a belt-and-braces safety net.
    for tu in writes_to_pend:
        raw_args = dict(tu.get('input') or {})
        if conversation.case_id is not None:
            raw_args['case_identifier'] = int(conversation.case_id)
        if conversation.war_room_id is not None:
            raw_args['war_room_id'] = int(conversation.war_room_id)
        row = case_chat_biz.create_pending_tool_call(
            conversation=conversation,
            assistant_message=assistant_msg,
            tool_use_id=tu['id'],
            tool_name=tu['name'],
            arguments=raw_args,
        )
        emit(ChatEvent('assistant_tool_start', {
            'pending_tool_call_id': row.id,
            'tool_use_id': tu['id'],
            'tool_name': tu['name'],
            'arguments': tu['input'] or {},
            'is_write': True,
            'pending': True,
        }))

    if writes_to_pend:
        return _TurnResult(paused_on_write=True)

    # All-reads turn — the caller re-enters the loop with the
    # freshly-appended tool_result messages in history.
    return _TurnResult()


def _dispatch_and_track(
    *,
    conversation: CaseChatConversation,
    tool_use: dict[str, Any],
    emit: Emitter,
) -> Any:
    """Call `dispatch_tool_call` with the case_id override applied.

    Emits `assistant_tool_start` (auto-execute) + `assistant_tool_result`
    events for the SPA to render a collapsible tool card. Never lets
    the LLM's `case_identifier` argument through — the conversation's
    scope is authoritative and a mismatch is logged as a possible
    prompt-injection tell.
    """
    name = tool_use['name']
    args = dict(tool_use.get('input') or {})

    # Scope override. See §7 of the plan.
    if conversation.case_id is not None:
        llm_case = args.get('case_identifier')
        if llm_case is not None and int(llm_case) != int(conversation.case_id):
            logger.warning(
                'chatbot: LLM emitted case_identifier=%s for a '
                'conversation scoped to case_id=%s (tool=%s, conv=%s) '
                '— overriding. Possible prompt-injection.',
                llm_case, conversation.case_id, name, conversation.id,
            )
        args['case_identifier'] = int(conversation.case_id)
    if conversation.war_room_id is not None:
        llm_wr = args.get('war_room_id')
        if llm_wr is not None and int(llm_wr) != int(conversation.war_room_id):
            logger.warning(
                'chatbot: LLM emitted war_room_id=%s for a '
                'conversation scoped to war_room_id=%s (tool=%s, conv=%s) '
                '— overriding. Possible prompt-injection.',
                llm_wr, conversation.war_room_id, name, conversation.id,
            )
        args['war_room_id'] = int(conversation.war_room_id)

    emit(ChatEvent('assistant_tool_start', {
        'tool_use_id': tool_use['id'],
        'tool_name': name,
        'arguments': args,
        'is_write': tool_classification.is_write(name),
        'pending': False,
    }))

    try:
        result = dispatch_tool_call(name, args)
    except MCPError as exc:
        emit(ChatEvent('assistant_tool_result', {
            'tool_use_id': tool_use['id'],
            'tool_name': name,
            'error': exc.message,
        }))
        return {'error': exc.message}
    except Exception as exc:
        logger.exception('chatbot: dispatch failed for %s', name)
        emit(ChatEvent('assistant_tool_result', {
            'tool_use_id': tool_use['id'],
            'tool_name': name,
            'error': f'Internal error: {exc}',
        }))
        return {'error': str(exc)}

    # Audit — every chatbot-driven tool call also hits track_activity
    # so the case activity log shows what the assistant did.
    caseid = conversation.case_id
    try:
        track_activity(
            f'chat:{name} tool_use_id={tool_use["id"]}',
            caseid=caseid,
            ctx_less=caseid is None,
            display_in_ui=caseid is not None,
        )
    except Exception:
        logger.exception('chatbot: track_activity failed for %s', name)

    # Emit is best-effort — if it fails (JSON encoding bug, socket
    # dropped mid-turn, etc.), we still return `result` so the caller
    # persists the tool_result message. Losing that persistence would
    # leave an orphan tool_use in history and Anthropic would reject
    # the next request with "tool_use ids were found without
    # tool_result blocks".
    try:
        emit(ChatEvent('assistant_tool_result', {
            'tool_use_id': tool_use['id'],
            'tool_name': name,
            'result': result,
        }))
    except Exception:
        logger.exception('chatbot: assistant_tool_result emit failed for %s', name)
    return result


def _build_request_snapshot(
    system: str,
    history: list[ChatMessage],
    tools: list[ToolSpec],
) -> dict:
    """Build a stripped snapshot of what is sent to the model this turn.

    Captures the system prompt, the full tools list (names + descriptions
    + schemas), and only the triggering user message — not the full
    conversation history. Stored on the egress-audit row so admins can
    verify exactly which tools the model received without replaying the
    whole conversation.
    """
    last_user = next(
        (m.content for m in reversed(history) if m.role == 'user'), None
    )
    return {
        'system': system,
        'tools': [
            {
                'name': t.name,
                'description': t.description,
                'input_schema': t.input_schema,
            }
            for t in tools
        ],
        'user_message': _without_file_bytes(last_user),
    }


def _without_file_bytes(content: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    """The user message as audited: an attached file is named, its bytes
    are not copied into the audit row."""
    if content is None:
        return None
    out: list[dict[str, Any]] = []
    for block in content:
        source = block.get('source') if isinstance(block, dict) else None
        if isinstance(source, dict) and 'data' in source:
            block = {**block, 'source': {**source, 'data': f'<{len(source["data"])} base64 characters>'}}
        out.append(block)
    return out


def _event_bytes(event: LLMEvent) -> int:
    """Rough byte count of a single event for the audit row's
    response_bytes tally. Cheap; the DPO view doesn't need
    protocol-level precision."""
    if isinstance(event, TextDelta):
        return len(event.text)
    if isinstance(event, ToolUseEnd):
        return len(event.name) + len(json.dumps(event.arguments, default=str))
    return 0


# Re-export so approve_tool.py can call the loop from its handler
# without a circular import through the namespace module.
__all__ = ['run_one_iteration', 'absorb_user_texts', 'ChatEvent', 'Emitter']

# Silence "imported but unused" — kept in case an unclassified tool
# escapes classification at runtime and we need to re-raise.
_ = UnclassifiedToolError
