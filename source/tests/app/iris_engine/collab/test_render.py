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

"""Unit tests for app.iris_engine.collab.render.

All functions under test are pure Python — no DB, no Flask context, no mocks.
"""

from unittest import TestCase

from pycrdt import Doc, Text, XmlElement, XmlFragment, XmlText

from app.iris_engine.collab.mentions import build_mention_span
from app.iris_engine.collab.render import (
    _escape_markdown,
    _repair_flushed_mark_markup,
    _rewrite_datastore_urls,
    _rewrite_legacy_case_urls,
    _strip_fontawesome_tags,
    _unescape_legacy_markdown_links,
    _unflatten_pipe_tables,
    append_markdown_to_ydoc_update,
    markdown_to_ydoc_update,
    replace_markdown_in_ydoc_update,
    normalize_markdown,
    ydoc_update_is_legacy_tree,
    ydoc_update_to_markdown,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _roundtrip(md: str) -> str:
    """Convenience: md → ydoc update → md."""
    return ydoc_update_to_markdown(markdown_to_ydoc_update(md))


def _append(base_md: str, md: str) -> str:
    """Convenience: seed a doc from `base_md`, append `md`, render back."""
    state = markdown_to_ydoc_update(base_md)
    return ydoc_update_to_markdown(append_markdown_to_ydoc_update(state, md))


# ---------------------------------------------------------------------------
# _escape_markdown
# ---------------------------------------------------------------------------

class TestEscapeMarkdown(TestCase):

    def test_plain_text_is_unchanged(self):
        self.assertEqual('hello world', _escape_markdown('hello world'))

    def test_backslash_is_escaped(self):
        self.assertEqual(r'\\path\\to', _escape_markdown(r'\path\to'))

    def test_backtick_is_escaped(self):
        self.assertEqual(r'\`code\`', _escape_markdown('`code`'))

    def test_asterisk_is_escaped(self):
        self.assertEqual(r'\*bold\*', _escape_markdown('*bold*'))

    def test_underscore_is_escaped(self):
        self.assertEqual(r'\_italic\_', _escape_markdown('_italic_'))

    def test_open_bracket_is_escaped(self):
        self.assertEqual(r'\[link\]', _escape_markdown('[link]'))

    def test_less_than_is_escaped(self):
        # Only `<` is in the escape set; `>` is not (only meaningful at line
        # start for blockquotes, left unescaped to avoid visual noise).
        self.assertEqual(r'\<tag>', _escape_markdown('<tag>'))

    def test_empty_string_is_returned_unchanged(self):
        self.assertEqual('', _escape_markdown(''))

    def test_multiple_special_chars_each_escaped(self):
        result = _escape_markdown('`*_')
        self.assertEqual(r'\`\*\_', result)

    def test_chars_not_in_escape_set_are_left_alone(self):
        # ! . - + > # are deliberately NOT escaped (only matter at line start)
        self.assertEqual('!.-+>#', _escape_markdown('!.-+>#'))

    def test_digits_and_letters_pass_through(self):
        self.assertEqual('abc123', _escape_markdown('abc123'))


# ---------------------------------------------------------------------------
# _rewrite_legacy_case_urls
# ---------------------------------------------------------------------------

class TestRewriteLegacyCaseUrls(TestCase):

    # --- fast-path guards ---

    def test_string_without_case_is_returned_unchanged(self):
        s = 'no links here'
        self.assertIs(s, _rewrite_legacy_case_urls(s))

    def test_string_with_case_but_no_cid_is_returned_unchanged(self):
        s = '/case/iocs?ioc_id=5'
        self.assertEqual(s, _rewrite_legacy_case_urls(s))

    def test_empty_string_is_returned_unchanged(self):
        self.assertEqual('', _rewrite_legacy_case_urls(''))

    # --- subpath rewrites ---

    def test_iocs_url_rewritten(self):
        result = _rewrite_legacy_case_urls('/case/iocs?cid=3&ioc_id=7')
        self.assertEqual('/case/3/iocs/7', result)

    def test_assets_url_rewritten(self):
        result = _rewrite_legacy_case_urls('/case/assets?cid=10&asset_id=42')
        self.assertEqual('/case/10/assets/42', result)

    def test_tasks_url_rewritten(self):
        # tasks used `id=` not `task_id=` in v2.4
        result = _rewrite_legacy_case_urls('/case/tasks?cid=2&id=99')
        self.assertEqual('/case/2/tasks/99', result)

    def test_notes_url_rewritten(self):
        result = _rewrite_legacy_case_urls('/case/notes?cid=5&note_id=11')
        self.assertEqual('/case/5/notes/11', result)

    def test_evidences_url_rewritten(self):
        result = _rewrite_legacy_case_urls('/case/evidences?cid=1&evidence_id=3')
        self.assertEqual('/case/1/evidences/3', result)

    def test_timeline_url_rewritten_without_object_id(self):
        result = _rewrite_legacy_case_urls('/case/timeline?cid=7')
        self.assertEqual('/case/7/timeline', result)

    def test_bare_case_url_rewritten(self):
        result = _rewrite_legacy_case_urls('/case?cid=4')
        self.assertEqual('/case/4', result)

    def test_subpath_without_item_id_falls_back_to_section_only(self):
        # cid present, item id absent → /case/{cid}/{section}
        result = _rewrite_legacy_case_urls('/case/iocs?cid=3')
        self.assertEqual('/case/3/iocs', result)

    def test_multiple_urls_in_one_string_all_rewritten(self):
        md = ('See [IOC](/case/iocs?cid=1&ioc_id=2) '
              'and [case](/case?cid=1).')
        result = _rewrite_legacy_case_urls(md)
        self.assertIn('/case/1/iocs/2', result)
        self.assertIn('/case/1', result)

    def test_cid_as_param_after_ampersand_works(self):
        # cid may appear after the resource id param
        result = _rewrite_legacy_case_urls('/case/iocs?ioc_id=9&cid=5')
        self.assertEqual('/case/5/iocs/9', result)

    def test_unrelated_text_after_url_is_preserved(self):
        result = _rewrite_legacy_case_urls('Go to /case?cid=2 now.')
        self.assertIn('/case/2', result)
        self.assertIn('now.', result)


# ---------------------------------------------------------------------------
# _unflatten_pipe_tables
# ---------------------------------------------------------------------------

class TestUnflattenPipeTables(TestCase):

    def test_string_without_pipe_returned_unchanged(self):
        s = 'no table here'
        self.assertIs(s, _unflatten_pipe_tables(s))

    def test_string_without_dashes_returned_unchanged(self):
        s = '| a | b |'
        self.assertIs(s, _unflatten_pipe_tables(s))

    def test_well_formed_multiline_table_passes_through_intact(self):
        table = '| A | B |\n| --- | --- |\n| 1 | 2 |'
        result = _unflatten_pipe_tables(table)
        # All original cells still present
        self.assertIn('| A | B |', result)
        self.assertIn('| --- | --- |', result)
        self.assertIn('| 1 | 2 |', result)

    def test_flattened_table_is_split_into_rows(self):
        flat = '| A | B || --- | --- || 1 | 2 |'
        result = _unflatten_pipe_tables(flat)
        lines = [line for line in result.split('\n') if line.strip()]
        # Should be at least 3 separate lines now
        self.assertGreaterEqual(len(lines), 3)

    def test_flattened_table_rows_each_contain_pipe(self):
        flat = '| H1 | H2 || --- | --- || R1 | R2 |'
        result = _unflatten_pipe_tables(flat)
        lines = [line for line in result.split('\n') if line.strip()]
        for line in lines:
            self.assertIn('|', line)

    def test_plain_prose_with_dashes_is_not_split(self):
        # A line with dashes but no separator pattern should not be mangled
        prose = 'The score is 10 -- 5 and | denotes or.'
        result = _unflatten_pipe_tables(prose)
        self.assertIn('The score', result)

    def test_empty_input_returns_empty(self):
        self.assertEqual('', _unflatten_pipe_tables(''))

    def test_output_is_a_gfm_table(self):
        """After unflattening: header, separator and row, one per line."""
        flat = '| Col1 | Col2 || --- | --- || val1 | val2 |'
        result = _unflatten_pipe_tables(flat)
        lines = [line.strip() for line in result.split('\n') if line.strip()]
        self.assertEqual(3, len(lines))
        self.assertIn('Col1', lines[0])
        self.assertRegex(lines[1], r'^\|(\s*:?-+:?\s*\|)+$')
        self.assertIn('val1', lines[2])


# ---------------------------------------------------------------------------
# _strip_fontawesome_tags
# ---------------------------------------------------------------------------

class TestStripFontAwesomeTags(TestCase):
    """Mirror of `stripFontAwesomeTags` in the frontend's legacy-content.ts."""

    def test_string_without_i_tag_is_returned_unchanged(self):
        s = 'no icons here'
        self.assertIs(s, _strip_fontawesome_tags(s))

    def test_empty_string_is_returned_unchanged(self):
        self.assertEqual('', _strip_fontawesome_tags(''))

    def test_single_quoted_fa_solid_tag_is_stripped(self):
        result = _strip_fontawesome_tags("<i class='fa-solid fa-bell'></i> #106")
        self.assertEqual(' #106', result)

    def test_double_quoted_fa_tag_is_stripped(self):
        result = _strip_fontawesome_tags('<i class="fa-solid fa-virus-covid"></i> [DS] f.zip')
        self.assertEqual(' [DS] f.zip', result)

    def test_tag_inside_markdown_link_text_is_stripped(self):
        md = "[<i class='fa-solid fa-bell'></i> #106](/alerts?alert_ids=106)"
        self.assertEqual('[ #106](/alerts?alert_ids=106)', _strip_fontawesome_tags(md))

    def test_uppercase_tag_is_stripped(self):
        self.assertEqual('', _strip_fontawesome_tags('<I CLASS="FA-SOLID FA-BELL"></I>'))

    def test_whitespace_between_open_and_close_is_tolerated(self):
        self.assertEqual('', _strip_fontawesome_tags("<i class='fa-bell'>  </i>"))

    def test_multiple_tags_all_stripped(self):
        md = "<i class='fa-bell'></i>a<i class='fa-bug'></i>b"
        self.assertEqual('ab', _strip_fontawesome_tags(md))

    def test_non_fontawesome_i_tag_is_left_alone(self):
        md = '<i class="italic"></i>'
        self.assertEqual(md, _strip_fontawesome_tags(md))

    def test_i_tag_with_text_content_is_left_alone(self):
        # Only self-empty icon tags are stripped — a tag with real content
        # would lose that content.
        md = "<i class='fa-bell'>text</i>"
        self.assertEqual(md, _strip_fontawesome_tags(md))

    def test_extra_attributes_around_class_are_tolerated(self):
        md = '<i id="x" class="fa-solid fa-bell" title="t"></i>done'
        self.assertEqual('done', _strip_fontawesome_tags(md))


# ---------------------------------------------------------------------------
# _unescape_legacy_markdown_links
# ---------------------------------------------------------------------------

class TestUnescapeLegacyMarkdownLinks(TestCase):
    """Mirror of `unescapeLegacyMarkdownLinks` in the frontend's legacy-content.ts."""

    def test_string_without_link_is_returned_unchanged(self):
        s = 'no links here'
        self.assertIs(s, _unescape_legacy_markdown_links(s))

    def test_escaped_image_brackets_are_unescaped(self):
        self.assertEqual('![a](u)', _unescape_legacy_markdown_links('!\\[a\\](u)'))

    def test_escaped_link_brackets_are_unescaped(self):
        self.assertEqual('[a](u)', _unescape_legacy_markdown_links('\\[a\\](u)'))

    def test_backticks_around_alt_are_dropped(self):
        self.assertEqual('![f.png](u)', _unescape_legacy_markdown_links('!\\[`f.png`\\](u)'))

    def test_escaped_brackets_not_followed_by_paren_are_left_alone(self):
        md = 'see \\[note\\] (later)'
        self.assertEqual(md, _unescape_legacy_markdown_links(md))

    def test_percent_size_suffix_is_stripped(self):
        self.assertEqual('![a](u)', _unescape_legacy_markdown_links('![a](u =100%x40%)'))

    def test_pixel_size_suffix_is_stripped(self):
        self.assertEqual('![a](u)', _unescape_legacy_markdown_links('![a](u =300x200)'))

    def test_size_suffix_on_link_is_left_alone(self):
        md = '[a](u =100%x40%)'
        self.assertEqual(md, _unescape_legacy_markdown_links(md))


# ---------------------------------------------------------------------------
# _rewrite_datastore_urls
# ---------------------------------------------------------------------------

class TestRewriteDatastoreUrls(TestCase):
    """Mirror of `rewriteDatastoreUrls` in the frontend's legacy-content.ts."""

    def test_string_without_datastore_url_is_returned_unchanged(self):
        s = '/case/1/notes'
        self.assertIs(s, _rewrite_datastore_urls(s))

    def test_url_with_cid_is_rewritten(self):
        self.assertEqual('/api/v2/cases/3465/datastore/files/6768',
                         _rewrite_datastore_urls('/datastore/file/view/6768?cid=3465'))

    def test_cid_after_other_params_is_found(self):
        self.assertEqual('/api/v2/cases/2/datastore/files/1',
                         _rewrite_datastore_urls('/datastore/file/view/1?x=y&cid=2'))

    def test_url_without_query_is_left_alone(self):
        md = '![a](/datastore/file/view/1)'
        self.assertEqual(md, _rewrite_datastore_urls(md))

    def test_url_without_cid_is_left_alone(self):
        md = '/datastore/file/view/1?x=y'
        self.assertEqual(md, _rewrite_datastore_urls(md))

    def test_link_target_is_rewritten_in_place(self):
        self.assertEqual('[f](/api/v2/cases/2/datastore/files/1) after',
                         _rewrite_datastore_urls('[f](/datastore/file/view/1?cid=2) after'))


class TestLegacyImagesSeed(TestCase):
    """A v2 image with a size suffix must seed as a valid markdown image."""

    def test_sized_legacy_image_seeds_as_image(self):
        md = '![NYeQ5](/datastore/file/view/6768?cid=3465 =100%x40%)\n'
        self.assertEqual('![NYeQ5](/api/v2/cases/3465/datastore/files/6768)', _roundtrip(md).strip())

    def test_image_previously_flushed_as_escaped_text_is_recovered(self):
        # What a document seeded by the old parser flushed back to the
        # source column: the literal text, with brackets escaped.
        md = '!\\[NYeQ5\\](/datastore/file/view/6768?cid=3465 =100%x40%)\n'
        self.assertEqual('![NYeQ5](/api/v2/cases/3465/datastore/files/6768)', _roundtrip(md).strip())


# ---------------------------------------------------------------------------
# Legacy documents — the ProseMirror tree TipTap's y-prosemirror wrote
# ---------------------------------------------------------------------------

def _client_paragraph(*runs) -> bytes:
    """A one-paragraph doc shaped like y-prosemirror writes it: one
    XmlText whose runs carry marks as formatting."""
    doc = Doc()
    frag = XmlFragment()
    doc['prosemirror'] = frag
    paragraph = frag.children.append(XmlElement('paragraph'))
    text = paragraph.children.append(XmlText())
    for chunk, formatting in runs:
        text.insert(len(text), chunk, formatting)
    return doc.get_update()


def _legacy_doc(build) -> bytes:
    """A legacy doc whose fragment `build(frag)` fills."""
    doc = Doc()
    frag = XmlFragment()
    doc['prosemirror'] = frag
    build(frag)
    return doc.get_update()


def _legacy_heading_and_chip(frag):
    heading = frag.children.append(XmlElement('heading', {'level': 2}))
    heading.children.append(XmlText('Findings'))
    paragraph = frag.children.append(XmlElement('paragraph'))
    paragraph.children.append(XmlText('see '))
    paragraph.children.append(XmlElement('caseMention',
                                         {'kind': 'alert', 'id': '7', 'label': 'Alert #7'}))


_CHIP_LINK = {
    'href': 'https://iris.i/api/v2/cases/3465/datastore/files/6771',
    'target': '_blank',
    'rel': 'noopener noreferrer nofollow',
    'class': 'text-blue-500 underline',
    'title': None,
}


class TestLegacyTreeRender(TestCase):
    """`ydoc_update_to_markdown` still reads documents seeded as a tree:
    it is how their unflushed edits are carried over to the text."""

    def test_client_link_renders_as_markdown_link(self):
        update = _client_paragraph(('see ', None), ('f.png', {'link': _CHIP_LINK}))
        self.assertEqual('see [f.png](https://iris.i/api/v2/cases/3465/datastore/files/6771)\n',
                         ydoc_update_to_markdown(update))

    def test_client_link_title_is_kept(self):
        update = _client_paragraph(('f', {'link': {**_CHIP_LINK, 'title': 'f.png'}}))
        self.assertIn('[f](https://iris.i/api/v2/cases/3465/datastore/files/6771 "f.png")',
                      ydoc_update_to_markdown(update))

    def test_client_bold_renders_as_markdown_bold(self):
        update = _client_paragraph(('a ', None), ('b', {'bold': {}}))
        self.assertEqual('a **b**\n', ydoc_update_to_markdown(update))

    def test_client_bold_italic_nest(self):
        update = _client_paragraph(('b', {'bold': {}, 'italic': {}}))
        self.assertEqual('***b***\n', ydoc_update_to_markdown(update))

    def test_client_code_is_not_escaped(self):
        update = _client_paragraph(('a_b', {'code': {}}))
        self.assertEqual('`a_b`\n', ydoc_update_to_markdown(update))

    def test_whitespace_stays_outside_emphasis(self):
        update = _client_paragraph(('a', None), (' b ', {'bold': {}}), ('c', None))
        self.assertEqual('a **b** c\n', ydoc_update_to_markdown(update))

    def test_runs_under_one_link_share_it(self):
        update = _client_paragraph(('a ', {'link': _CHIP_LINK}),
                                   ('b', {'link': _CHIP_LINK, 'bold': {}}))
        self.assertEqual('[a **b**](https://iris.i/api/v2/cases/3465/datastore/files/6771)\n',
                         ydoc_update_to_markdown(update))

    def test_no_pseudo_xml_is_rendered(self):
        update = _client_paragraph(('x', {'link': _CHIP_LINK, 'bold': {}}))
        self.assertNotIn('<', ydoc_update_to_markdown(update))

    def test_unknown_mark_keeps_its_text(self):
        update = _client_paragraph(('u', {'underline': {}}))
        self.assertEqual('u\n', ydoc_update_to_markdown(update))

    def test_headings_and_chips_render(self):
        update = _legacy_doc(_legacy_heading_and_chip)
        chip = build_mention_span('alert', '7', 'Alert #7')
        self.assertEqual(f'## Findings\n\nsee {chip}\n', ydoc_update_to_markdown(update))

    def test_legacy_tree_is_detected(self):
        self.assertTrue(ydoc_update_is_legacy_tree(_legacy_doc(_legacy_heading_and_chip)))

    def test_text_document_is_not_a_legacy_tree(self):
        self.assertFalse(ydoc_update_is_legacy_tree(markdown_to_ydoc_update('# T\n')))
        self.assertFalse(ydoc_update_is_legacy_tree(markdown_to_ydoc_update('')))
        self.assertFalse(ydoc_update_is_legacy_tree(None))

    def test_append_converts_the_tree_to_text_first(self):
        """Text written next to an untouched tree would hide it: the
        render reads the text whenever there is some."""
        state = append_markdown_to_ydoc_update(_legacy_doc(_legacy_heading_and_chip), 'Added.')
        chip = build_mention_span('alert', '7', 'Alert #7')
        self.assertEqual(f'## Findings\n\nsee {chip}\n\nAdded.', ydoc_update_to_markdown(state))
        self.assertFalse(ydoc_update_is_legacy_tree(state))

    def test_replace_converts_the_tree_to_text_first(self):
        state = replace_markdown_in_ydoc_update(_legacy_doc(_legacy_heading_and_chip), '# New\n')
        self.assertEqual('# New\n', ydoc_update_to_markdown(state))
        doc = Doc()
        frag = XmlFragment()
        doc['prosemirror'] = frag
        doc.apply_update(state)
        self.assertEqual(0, len(frag.children))


class TestRepairFlushedMarkMarkup(TestCase):
    """What the renderer flushed for marks before it read formatting."""

    def test_string_without_markup_is_returned_unchanged(self):
        s = 'no markup here'
        self.assertIs(s, _repair_flushed_mark_markup(s))

    def test_bold_is_repaired(self):
        self.assertEqual('a **b**', _repair_flushed_mark_markup('a \\<bold>b\\</bold>'))

    def test_leading_space_moves_outside_bold(self):
        self.assertEqual('a **b**', _repair_flushed_mark_markup('a\\<bold> b\\</bold>'))

    def test_nested_marks_are_repaired(self):
        self.assertEqual('***b***', _repair_flushed_mark_markup('\\<bold>\\<italic>b\\</italic>\\</bold>'))

    def test_code_is_unescaped(self):
        self.assertEqual('`a_b`', _repair_flushed_mark_markup('\\<code>a\\_b\\</code>'))

    def test_underline_keeps_its_text(self):
        self.assertEqual('u', _repair_flushed_mark_markup('\\<underline>u\\</underline>'))

    def test_link_is_repaired_with_unescaped_attributes(self):
        md = '\\<link target="\\_blank" href="https://x/a\\_b" title="null">f\\</link>'
        self.assertEqual('[f](https://x/a_b)', _repair_flushed_mark_markup(md))

    def test_emptied_chip_link_takes_its_title_as_label(self):
        md = ('\\<link rel="noopener noreferrer nofollow" target="\\_blank" title="HFm8.png" '
              'href="https://iris.i/api/v2/cases/3465/datastore/files/6771" '
              'class="text-blue-500 underline inline-flex"> \\</link>')
        self.assertEqual('[HFm8.png](https://iris.i/api/v2/cases/3465/datastore/files/6771 "HFm8.png")',
                         _repair_flushed_mark_markup(md))

    def test_unescaped_markup_is_repaired(self):
        self.assertEqual('[f](u)', _repair_flushed_mark_markup('<link href="u">f</link>'))

    def test_other_html_is_left_alone(self):
        md = '\\<span>x\\</span>'
        self.assertEqual(md, _repair_flushed_mark_markup(md))

    def test_reseed_recovers_a_link(self):
        md = 'see \\<link href="https://x/u" title="null">f\\</link>\n'
        self.assertEqual('see [f](https://x/u)\n', _roundtrip(md))


# ---------------------------------------------------------------------------
# markdown_to_ydoc_update / ydoc_update_to_markdown — the doc IS the markdown
# ---------------------------------------------------------------------------

class TestRoundTripEmpty(TestCase):

    def test_empty_string_produces_empty_string(self):
        self.assertEqual('', _roundtrip(''))

    def test_none_update_produces_empty_string(self):
        self.assertEqual('', ydoc_update_to_markdown(None))

    def test_empty_bytes_produces_empty_string(self):
        self.assertEqual('', ydoc_update_to_markdown(b''))

    def test_markdown_to_ydoc_returns_bytes(self):
        self.assertIsInstance(markdown_to_ydoc_update('# T\n'), bytes)

    def test_markdown_to_ydoc_with_none_does_not_crash(self):
        self.assertEqual('', ydoc_update_to_markdown(markdown_to_ydoc_update(None)))


class TestRoundTripIsTheIdentity(TestCase):
    """The editor edits the markdown source: what is seeded is what the
    user sees and what flushes back, formatting included."""

    def test_markdown_is_kept_verbatim(self):
        for md in (
            '# Title\n\nParagraph.\n',
            '* star bullets\n* stay stars\n',
            'no terminal newline',
            'line one\nline two\n',
            'a_b and 2*3 are not escaped\n',
            '```python\ndef f():\n    return "**not bold**"\n```\n',
            '> quote\n>\n> - list in quote\n',
            '1. one\n1. one again\n',
            '| A | B |\n|---|:-:|\n| 1 | 2 |\n',
            '<details><summary>raw html</summary>stays text</details>\n',
            'emoji 😀 and accents éà\n',
        ):
            with self.subTest(md=md):
                self.assertEqual(md, _roundtrip(md))

    def test_chips_are_kept_verbatim(self):
        chip = build_mention_span('alert', 106, 'Alert #106')
        md = f'## {chip}\n\n- see {chip} and **{chip}**\n'
        self.assertEqual(md, _roundtrip(md))

    def test_crlf_line_endings_become_lf(self):
        self.assertEqual('a\n\nb\n', _roundtrip('a\r\n\r\nb\r\n'))

    def test_legacy_shapes_are_cleaned(self):
        md = "[<i class='fa-solid fa-bell'></i> #106](/case/iocs?cid=8&ioc_id=3)\n"
        result = _roundtrip(md)
        self.assertNotIn('fa-solid', result)
        self.assertNotIn('cid=8', result)

    def test_normalize_markdown_is_the_seeded_content(self):
        md = 'a\r\nb [x](/case?cid=8)'
        self.assertEqual(_roundtrip(md), normalize_markdown(md))
        self.assertEqual('', normalize_markdown(None))


# ---------------------------------------------------------------------------
# append_markdown_to_ydoc_update
# ---------------------------------------------------------------------------

class TestAppendMarkdownToYdocUpdate(TestCase):
    """Server-side append into an already-seeded Y.Doc.

    Once a `collab_doc` row exists the Y.Doc is authoritative and the
    source column is ignored on re-open, so a backend feature that only
    appends to the column (alert merge writing its mention chip into
    `cases.description`) loses the append on the next flush. This
    primitive is how that append reaches the CRDT instead.
    """

    # --- empty / absent starting state ---

    def test_append_to_none_state_matches_a_fresh_build(self):
        md = '# Heading\n\nA paragraph.\n'
        # Bytes can't be compared — Yjs stamps a random client id into
        # every update — so the contract is content equivalence.
        self.assertEqual(
            ydoc_update_to_markdown(markdown_to_ydoc_update(md)),
            ydoc_update_to_markdown(append_markdown_to_ydoc_update(None, md)),
        )

    def test_append_to_empty_bytes_matches_a_fresh_build(self):
        md = '- one\n- two\n'
        self.assertEqual(
            ydoc_update_to_markdown(markdown_to_ydoc_update(md)),
            ydoc_update_to_markdown(append_markdown_to_ydoc_update(b'', md)),
        )

    def test_append_to_the_update_of_an_empty_doc_matches_a_fresh_build(self):
        # `ensure_snapshot` stores the update of an EMPTY doc when the
        # source column is blank; appending to that must not differ from
        # seeding the same markdown from scratch.
        md = 'Just text.\n'
        empty_state = markdown_to_ydoc_update('')
        self.assertEqual(
            ydoc_update_to_markdown(markdown_to_ydoc_update(md)),
            ydoc_update_to_markdown(append_markdown_to_ydoc_update(empty_state, md)),
        )

    def test_appending_empty_markdown_leaves_the_document_untouched(self):
        base = '# Title\n\nBody.\n'
        self.assertEqual(base, _append(base, ''))

    # --- ordering and preservation ---

    def test_original_blocks_are_preserved(self):
        base = '# Case summary\n\nInitial findings.\n'
        result = _append(base, 'Appended line.')
        self.assertIn('# Case summary', result)
        self.assertIn('Initial findings.', result)

    def test_appended_blocks_come_last(self):
        result = _append('# Case summary\n\nInitial findings.\n', 'Appended line.')
        self.assertTrue(result.startswith('# Case summary'))
        self.assertEqual('Appended line.', result.rstrip('\n').split('\n')[-1])

    def test_append_result_equals_the_concatenated_markdown(self):
        base = '# Title\n\nFirst.\n'
        added = 'Second.'
        self.assertEqual(base + '\n' + added, _append(base, added))

    def test_append_starts_a_block_of_its_own(self):
        self.assertEqual('a\n\nb', _append('a', 'b'))
        self.assertEqual('a\n\nb', _append('a\n', 'b'))
        self.assertEqual('a\n\nb', _append('a\n\n', 'b'))

    def test_multi_block_append_keeps_its_internal_order(self):
        result = _append('Intro.\n', '## Section\n\n- a\n- b\n')
        lines = [line for line in result.split('\n') if line]
        self.assertEqual(['Intro.', '## Section', '- a', '- b'], lines)

    def test_append_into_a_document_with_a_table_keeps_the_table(self):
        base = '| A | B |\n| --- | --- |\n| 1 | 2 |\n'
        result = _append(base, 'After the table.')
        self.assertIn('| A | B |', result)
        self.assertIn('| 1 | 2 |', result)
        self.assertEqual('After the table.', result.rstrip('\n').split('\n')[-1])

    # --- mention chips ---

    def test_appended_alert_chip_survives_the_append_and_the_render(self):
        """The exact shape `merge_alert_in_case` writes on escalation."""
        chip = build_mention_span('alert', 106, 'Alert #106')
        result = _append('# Case summary\n\nInitial findings.\n',
                         f'{chip} *escalated by alice*')
        self.assertIn(chip, result)
        self.assertIn('*escalated by alice*', result)
        self.assertIn('Initial findings.', result)

    def test_a_chip_already_in_the_document_is_not_disturbed_by_an_append(self):
        first = build_mention_span('alert', 1, 'Alert #1')
        second = build_mention_span('alert', 2, 'Alert #2')
        result = _append(f'{first}\n', second)
        self.assertIn(first, result)
        self.assertIn(second, result)
        self.assertLess(result.index(first), result.index(second))

    def test_appended_chip_survives_a_second_full_round_trip(self):
        # The flush path renders the Y.Doc to markdown and re-seeds from
        # it later; the chip must be stable across that.
        chip = build_mention_span('alert', 42, 'Alert #42')
        once = _append('Base.\n', f'{chip} *escalated by bob*')
        self.assertEqual(once, _roundtrip(once))

    # --- repeated appends (the batch-merge shape) ---

    def test_appending_twice_produces_both_fragments_in_order(self):
        first = build_mention_span('alert', 1, 'Alert #1')
        second = build_mention_span('alert', 2, 'Alert #2')
        state = markdown_to_ydoc_update('# Case summary\n')
        state = append_markdown_to_ydoc_update(state, f'{first} *escalated by alice*')
        state = append_markdown_to_ydoc_update(state, f'{second} *escalated by bob*')
        result = ydoc_update_to_markdown(state)
        self.assertIn('# Case summary', result)
        self.assertIn(first, result)
        self.assertIn(second, result)
        self.assertLess(result.index(first), result.index(second))

    def test_appending_twice_is_content_stable(self):
        state = markdown_to_ydoc_update('Base.\n')
        state = append_markdown_to_ydoc_update(state, 'One.')
        state = append_markdown_to_ydoc_update(state, 'Two.')
        result = ydoc_update_to_markdown(state)
        self.assertEqual('Base.\n\nOne.\n\nTwo.', result)
        # Re-seeding from the rendered markdown yields the same document.
        self.assertEqual(result, _roundtrip(result))

    def test_repeated_appends_of_the_same_markdown_accumulate(self):
        state = markdown_to_ydoc_update('')
        for _ in range(3):
            state = append_markdown_to_ydoc_update(state, 'line')
        self.assertEqual('line\n\nline\n\nline', ydoc_update_to_markdown(state))

    # --- returned value shape ---

    def test_the_return_value_is_a_full_state_not_a_delta(self):
        # `collab_doc.y_state` holds a full state, so the return value has
        # to render standalone — applying it to a fresh Doc must yield the
        # whole document, original blocks included.
        state = append_markdown_to_ydoc_update(
            markdown_to_ydoc_update('# Original\n'), 'Added.',
        )
        self.assertIsInstance(state, bytes)
        self.assertIn('# Original', ydoc_update_to_markdown(state))

    # --- the cleaning pre-passes must run on appended markdown too ---

    def test_legacy_case_urls_in_appended_markdown_are_rewritten(self):
        result = _append('Base.\n', 'see [the case](/case?cid=8)')
        self.assertIn('/case/8', result)
        self.assertNotIn('cid=8', result)

    def test_fontawesome_tags_in_appended_markdown_are_stripped(self):
        result = _append('Base.\n', "[<i class='fa-solid fa-bell'></i> #106](/alerts?alert_ids=106)")
        self.assertNotIn('fa-solid', result)
        self.assertIn('/alerts?alert_ids=106', result)

    def test_a_flattened_pipe_table_in_appended_markdown_is_unflattened(self):
        result = _append('Base.\n', '| A | B | |---|---| | 1 | 2 |')
        self.assertIn('| A | B |', result)
        self.assertIn('| 1 | 2 |', result)


# ---------------------------------------------------------------------------
# replace_markdown_in_ydoc_update
# ---------------------------------------------------------------------------

def _replace(base_md: str, md: str) -> str:
    """Convenience: seed a doc from `base_md`, replace it with `md`, render back."""
    state = markdown_to_ydoc_update(base_md)
    return ydoc_update_to_markdown(replace_markdown_in_ydoc_update(state, md))


class TestReplaceMarkdownInYdocUpdate(TestCase):
    """Server-side rewrite of an already-seeded Y.Doc (Yuki editing a note)."""

    def test_replace_drops_the_old_content(self):
        result = _replace('# Old title\n\nOld body.\n', '# New title\n\nNew body.\n')
        self.assertNotIn('Old', result)
        self.assertEqual(_roundtrip('# New title\n\nNew body.\n'), result)

    def test_replace_none_state_matches_a_fresh_build(self):
        md = '- one\n- two\n'
        self.assertEqual(
            ydoc_update_to_markdown(markdown_to_ydoc_update(md)),
            ydoc_update_to_markdown(replace_markdown_in_ydoc_update(None, md)),
        )

    def test_replace_with_empty_markdown_empties_the_document(self):
        self.assertEqual('', _replace('# Title\n\nBody.\n', '').strip())

    def test_replace_keeps_tables_and_chips(self):
        chip = build_mention_span('alert', 3, 'Alert #3')
        md = f'| A | B |\n| --- | --- |\n| 1 | 2 |\n\n{chip} seen.\n'
        result = _replace('Old.\n', md)
        self.assertIn('| 1 | 2 |', result)
        self.assertIn(chip, result)
        self.assertNotIn('Old.', result)

    def test_a_client_holding_the_old_state_converges_on_the_new_content(self):
        """The reason the old text is deleted in the CRDT rather than
        rebuilt from scratch: an editor still holding the old document
        must end up with the new content only, not old + new."""
        old = markdown_to_ydoc_update('# Draft\n\nMessy notes.\n')
        new = replace_markdown_in_ydoc_update(old, '# Clean\n\nTidy notes.\n')

        client = Doc()
        client['markdown'] = Text()
        client.apply_update(old)
        client.apply_update(new)
        result = ydoc_update_to_markdown(client.get_update())
        self.assertEqual(_roundtrip('# Clean\n\nTidy notes.\n'), result)

    def test_replace_runs_the_cleaning_pre_passes(self):
        result = _replace('Base.\n', 'see [the case](/case?cid=8)')
        self.assertIn('/case/8', result)

    def test_only_the_changed_span_is_replaced(self):
        """What an editor typed concurrently outside the rewritten span
        survives the merge."""
        old = markdown_to_ydoc_update('# Title\n\nBody one.\n\nFooter.\n')

        client = Doc()
        text = Text()
        client['markdown'] = text
        client.apply_update(old)
        text.insert(len(text), 'Typed meanwhile.\n')
        typed = client.get_update()

        new = replace_markdown_in_ydoc_update(old, '# Title\n\nBody two.\n\nFooter.\n')
        client.apply_update(new)
        self.assertEqual('# Title\n\nBody two.\n\nFooter.\nTyped meanwhile.\n',
                         ydoc_update_to_markdown(client.get_update()))

        server = Doc()
        server['markdown'] = Text()
        server.apply_update(new)
        server.apply_update(typed)
        self.assertEqual(str(text), str(server['markdown']))

    def test_identical_markdown_changes_nothing(self):
        old = markdown_to_ydoc_update('Same.\n')
        self.assertEqual('Same.\n', ydoc_update_to_markdown(replace_markdown_in_ydoc_update(old, 'Same.\n')))

    def test_multi_byte_characters_around_the_change(self):
        """pycrdt indexes text in UTF-8 bytes: offsets computed in
        characters would cut a character in two."""
        cases = [
            ('a😀é b', 'a😀Xé b'),
            ('😀😀😀', '😀😁😀'),
            ('é', ''),
            ('', 'ü'),
            ('日本語のノート', '日本のノート'),
            ('chip 🎉 end', 'chip 🎉🎉 end'),
        ]
        for before, after in cases:
            with self.subTest(before=before, after=after):
                self.assertEqual(after, _replace(before, after))
