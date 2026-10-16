#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU Lesser General Public
#  License as published by the Free Software Foundation; either
#  version 3 of the License, or (at your option) any later version.

"""Files an analyst sends to Yuki: what is accepted, and how it is sent.

The kind is read from the bytes, never from the name or the browser's
content type: an image (PNG, JPEG, GIF, WebP), a PDF, or text (UTF-8,
no NUL byte). Anything else is refused.

On the way to the provider, an attachment becomes Anthropic-shaped
content blocks, the shape the history is kept in: an `image` or a
`document` block with the base64 bytes, or a `text` block wrapping the
file. The OpenAI and Ollama adapters map them to their own wire.
"""
from __future__ import annotations

import base64
import io
from dataclasses import dataclass
from typing import Any

from PIL import Image
from PIL import UnidentifiedImageError


ATTACHMENT_KIND_IMAGE = 'image'
ATTACHMENT_KIND_PDF = 'pdf'
ATTACHMENT_KIND_TEXT = 'text'

# The providers cap an image at 5 MB; one ceiling for every kind keeps
# it simple to explain.
ATTACHMENT_MAX_BYTES = 5 * 1024 * 1024
ATTACHMENTS_MAX_PER_MESSAGE = 5
# A text file goes in the prompt as is: past this, it is cut.
ATTACHMENT_TEXT_MAX_CHARS = 100_000

_IMAGE_FORMATS = {
    'PNG': 'image/png',
    'JPEG': 'image/jpeg',
    'GIF': 'image/gif',
    'WEBP': 'image/webp',
}


class AttachmentRejectedError(ValueError):
    """The file can't be sent to Yuki; the message says why."""


@dataclass(frozen=True)
class SniffedAttachment:
    kind: str
    media_type: str


def attachments_sniff(raw: bytes) -> SniffedAttachment:
    """Tell what `raw` is, or raise `AttachmentRejectedError`."""
    if not raw:
        raise AttachmentRejectedError('The file is empty.')
    if len(raw) > ATTACHMENT_MAX_BYTES:
        raise AttachmentRejectedError(
            f'The file exceeds {ATTACHMENT_MAX_BYTES // (1024 * 1024)} MB.')
    if raw.startswith(b'%PDF-'):
        return SniffedAttachment(ATTACHMENT_KIND_PDF, 'application/pdf')
    image_type = _image_media_type(raw)
    if image_type is not None:
        return SniffedAttachment(ATTACHMENT_KIND_IMAGE, image_type)
    if _is_text(raw):
        return SniffedAttachment(ATTACHMENT_KIND_TEXT, 'text/plain')
    raise AttachmentRejectedError(
        'Only images (PNG, JPEG, GIF, WebP), PDF and text files can be sent to Yuki.')


def _image_media_type(raw: bytes) -> str | None:
    try:
        with Image.open(io.BytesIO(raw)) as image:
            media_type = _IMAGE_FORMATS.get(image.format or '')
            if media_type is None:
                return None
            image.verify()
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, Image.DecompressionBombError):
        return None
    return media_type


def _is_text(raw: bytes) -> bool:
    if b'\x00' in raw:
        return False
    try:
        raw.decode('utf-8')
    except UnicodeDecodeError:
        return False
    return True


def attachments_reference_block(attachment: Any) -> dict[str, Any]:
    """The block a user message keeps for an attachment: no bytes."""
    return {
        'type': 'attachment',
        'attachment_id': attachment.id,
        'filename': attachment.filename,
        'kind': attachment.kind,
        'media_type': attachment.media_type,
        'size': attachment.size,
    }


def attachments_expand(blocks: list[dict[str, Any]], attachments: dict[int, Any]) -> list[dict[str, Any]]:
    """Swap every `attachment` block for what the provider reads.

    `attachments` maps ids to rows (`filename`, `kind`, `media_type`,
    `data`). A missing row — the attachment was deleted — leaves a note.
    """
    out: list[dict[str, Any]] = []
    for block in blocks:
        if not (isinstance(block, dict) and block.get('type') == 'attachment'):
            out.append(block)
            continue
        attachment = attachments.get(block.get('attachment_id'))
        filename = _quote(str(block.get('filename') or 'file'))
        if attachment is None:
            out.append({'type': 'text', 'text': f'\n\n[Attachment "{filename}" is no longer available.]'})
            continue
        out.extend(_provider_blocks(attachment, filename))
    return out


def _provider_blocks(attachment: Any, filename: str) -> list[dict[str, Any]]:
    data = bytes(attachment.data)
    if attachment.kind == ATTACHMENT_KIND_TEXT:
        text = data.decode('utf-8', errors='replace').removeprefix('\ufeff')
        cut = ''
        if len(text) > ATTACHMENT_TEXT_MAX_CHARS:
            text = text[:ATTACHMENT_TEXT_MAX_CHARS]
            cut = f'\n[Cut after {ATTACHMENT_TEXT_MAX_CHARS} characters.]'
        return [{
            'type': 'text',
            'text': f'\n\n<attachment name="{filename}">\n{text}{cut}\n</attachment>',
        }]
    source = {
        'type': 'base64',
        'media_type': attachment.media_type,
        'data': base64.b64encode(data).decode('ascii'),
    }
    label = {'type': 'text', 'text': f'\n\nAttached file: "{filename}"'}
    if attachment.kind == ATTACHMENT_KIND_PDF:
        return [label, {'type': 'document', 'source': source, 'title': filename}]
    return [label, {'type': 'image', 'source': source}]


def _quote(filename: str) -> str:
    return filename.replace('"', "'").replace('\n', ' ').replace('\r', ' ')
