#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU Lesser General Public
#  License as published by the Free Software Foundation; either
#  version 3 of the License, or (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
#  Lesser General Public License for more details.
#
#  You should have received a copy of the GNU Lesser General Public License
#  along with this program; if not, write to the Free Software Foundation,
#  Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.

"""Files sent to Yuki: what is accepted, how each provider gets it, and
how a turn carries them. No network, no DB."""

import base64
import io
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock
from unittest.mock import patch

from PIL import Image

from app.blueprints.rest.v2.case_chat import loop
from app.blueprints.rest.v2.case_chat.namespace import _int_list
from app.iris_engine.llm import attachments
from app.iris_engine.llm.attachments import AttachmentRejectedError
from app.iris_engine.llm.attachments import attachments_expand
from app.iris_engine.llm.attachments import attachments_sniff
from app.iris_engine.llm.providers.base import ChatMessage
from app.iris_engine.llm.providers.ollama import _ours_to_ollama_messages
from app.iris_engine.llm.providers.openai import _ours_to_openai_messages


_LOOP = 'app.blueprints.rest.v2.case_chat.loop'


def _png() -> bytes:
    buffer = io.BytesIO()
    Image.new('RGB', (4, 4), 'red').save(buffer, format='PNG')
    return buffer.getvalue()


def _row(attachment_id, kind, media_type, data, filename='file'):
    return SimpleNamespace(id=attachment_id, kind=kind, media_type=media_type, data=data,
                           filename=filename, size=len(data))


def _ref(attachment_id, filename='file'):
    return {'type': 'attachment', 'attachment_id': attachment_id, 'filename': filename}


class TestSniff(TestCase):

    def test_image_should_be_recognised_from_its_bytes(self):
        sniffed = attachments_sniff(_png())
        self.assertEqual(('image', 'image/png'), (sniffed.kind, sniffed.media_type))

    def test_pdf_should_be_recognised(self):
        self.assertEqual('pdf', attachments_sniff(b'%PDF-1.7\n...').kind)

    def test_utf8_should_be_text(self):
        sniffed = attachments_sniff('2026-10-16 login from 10.0.0.1 — ok\n'.encode())
        self.assertEqual(('text', 'text/plain'), (sniffed.kind, sniffed.media_type))

    def test_binary_should_be_refused(self):
        with self.assertRaises(AttachmentRejectedError):
            attachments_sniff(b'MZ\x90\x00\x03\x00\x00\x00')

    def test_a_truncated_image_should_be_refused(self):
        with self.assertRaises(AttachmentRejectedError):
            attachments_sniff(_png()[:30] + b'\x00')

    def test_empty_and_oversized_files_should_be_refused(self):
        with self.assertRaises(AttachmentRejectedError):
            attachments_sniff(b'')
        with self.assertRaises(AttachmentRejectedError):
            attachments_sniff(b'a' * (attachments.ATTACHMENT_MAX_BYTES + 1))


class TestExpand(TestCase):

    def test_image_should_become_a_label_and_an_image_block(self):
        png = _png()
        blocks = attachments_expand(
            [{'type': 'text', 'text': 'what is this?'}, _ref(1, 'shot.png')],
            {1: _row(1, 'image', 'image/png', png, 'shot.png')})
        self.assertEqual('what is this?', blocks[0]['text'])
        self.assertIn('shot.png', blocks[1]['text'])
        self.assertEqual({'type': 'base64', 'media_type': 'image/png',
                          'data': base64.b64encode(png).decode()}, blocks[2]['source'])

    def test_pdf_should_become_a_document_block(self):
        blocks = attachments_expand([_ref(2, 'report.pdf')],
                                    {2: _row(2, 'pdf', 'application/pdf', b'%PDF-1.7', 'report.pdf')})
        self.assertEqual('document', blocks[1]['type'])
        self.assertEqual('report.pdf', blocks[1]['title'])

    def test_text_should_be_inlined_and_cut_when_long(self):
        data = ('x' * (attachments.ATTACHMENT_TEXT_MAX_CHARS + 10)).encode()
        blocks = attachments_expand([_ref(3, 'a"b.log')], {3: _row(3, 'text', 'text/plain', data)})
        self.assertEqual(1, len(blocks))
        self.assertIn('<attachment name="a\'b.log">', blocks[0]['text'])
        self.assertIn('[Cut after', blocks[0]['text'])

    def test_missing_attachment_should_leave_a_note(self):
        blocks = attachments_expand([_ref(4, 'gone.png')], {})
        self.assertIn('no longer available', blocks[0]['text'])


class TestProviders(TestCase):

    def _blocks(self):
        source = {'type': 'base64', 'media_type': 'image/png', 'data': 'AAA'}
        pdf = {'type': 'base64', 'media_type': 'application/pdf', 'data': 'BBB'}
        return [{'type': 'text', 'text': 'look'},
                {'type': 'image', 'source': source},
                {'type': 'document', 'source': pdf, 'title': 'r.pdf'}]

    def test_openai_should_send_content_parts_with_data_urls(self):
        [msg] = _ours_to_openai_messages(ChatMessage(role='user', content=self._blocks()))
        self.assertEqual({'type': 'text', 'text': 'look'}, msg['content'][0])
        self.assertEqual('data:image/png;base64,AAA', msg['content'][1]['image_url']['url'])
        self.assertEqual({'filename': 'r.pdf', 'file_data': 'data:application/pdf;base64,BBB'},
                         msg['content'][2]['file'])

    def test_openai_should_keep_plain_text_as_a_string(self):
        [msg] = _ours_to_openai_messages(ChatMessage(role='user', content=[{'type': 'text', 'text': 'hi'}]))
        self.assertEqual('hi', msg['content'])

    def test_ollama_should_send_images_beside_the_text_and_explain_the_pdf(self):
        [msg] = _ours_to_ollama_messages(ChatMessage(role='user', content=self._blocks()))
        self.assertEqual(['AAA'], msg['images'])
        self.assertTrue(msg['content'].startswith('look'))
        self.assertIn('does not read PDFs', msg['content'])


class TestTurn(TestCase):

    def test_absorbed_turn_should_carry_the_staged_attachments(self):
        biz = MagicMock()
        biz.list_pending_tool_call_ids.return_value = []
        staged = [_row(5, 'image', 'image/png', b'x', 'a.png')]
        biz.take_staged_attachments.return_value = staged
        with patch(f'{_LOOP}.case_chat_biz', biz), patch(f'{_LOOP}.iris_current_user'):
            loop.absorb_user_texts(conversation=SimpleNamespace(id=7), texts=['', 'see'], emit=MagicMock())
        content = biz.append_message.call_args.kwargs['content']
        self.assertEqual({'type': 'text', 'text': 'see'}, content[0])
        self.assertEqual({'type': 'attachment', 'attachment_id': 5, 'filename': 'a.png',
                          'kind': 'image', 'media_type': 'image/png', 'size': 1}, content[1])
        biz.link_attachments.assert_called_once_with(staged, biz.append_message.return_value)

    def test_attachments_alone_should_need_no_text_block(self):
        biz = MagicMock()
        biz.list_pending_tool_call_ids.return_value = []
        biz.take_staged_attachments.return_value = [_row(5, 'text', 'text/plain', b'x')]
        with patch(f'{_LOOP}.case_chat_biz', biz), patch(f'{_LOOP}.iris_current_user'):
            loop.absorb_user_texts(conversation=SimpleNamespace(id=7), texts=[''], emit=MagicMock())
        content = biz.append_message.call_args.kwargs['content']
        self.assertEqual(['attachment'], [b['type'] for b in content])

    def test_history_should_expand_attachments_of_user_messages(self):
        biz = MagicMock()
        biz.list_messages.return_value = [
            SimpleNamespace(role='user', content=[{'type': 'text', 'text': 'q'}, _ref(9, 'n.txt')]),
        ]
        biz.get_attachments_by_id.return_value = {9: _row(9, 'text', 'text/plain', b'hello', 'n.txt')}
        cfg = SimpleNamespace(max_turns_per_conversation=25, redact_ips=False,
                              redact_emails=False, redact_hashes=False)
        with patch(f'{_LOOP}.case_chat_biz', biz):
            history = loop._load_history(SimpleNamespace(id=7), cfg)
        biz.get_attachments_by_id.assert_called_once_with({9})
        self.assertIn('hello', history[0].content[1]['text'])

    def test_audit_snapshot_should_not_copy_the_file_bytes(self):
        history = [ChatMessage(role='user', content=[
            {'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/png', 'data': 'A' * 40}}])]
        snapshot = loop._build_request_snapshot('system', history, [])
        self.assertEqual('<40 base64 characters>', snapshot['user_message'][0]['source']['data'])


class TestSendPayload(TestCase):

    def test_attachment_ids_should_be_a_list_of_ints(self):
        self.assertEqual([], _int_list({}, 'attachment_ids'))
        self.assertEqual([1, 2], _int_list({'attachment_ids': [1, 2]}, 'attachment_ids'))
        self.assertIsNone(_int_list({'attachment_ids': '1'}, 'attachment_ids'))
        self.assertIsNone(_int_list({'attachment_ids': [1, '2']}, 'attachment_ids'))
        self.assertIsNone(_int_list({'attachment_ids': [True]}, 'attachment_ids'))
