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

"""Every built-in graph icon the code names ships where Flask serves /static."""

import re
from pathlib import Path
from unittest import TestCase

_SOURCE = Path(__file__).resolve().parents[2]
_GRAPH_ICONS = _SOURCE / 'static' / 'assets' / 'img' / 'graph'
_ICON_NAME_RE = re.compile(r"""['"]([\w-]+\.png)['"]""")


def _icons_named_in_the_code():
    names = set()
    for path in (_SOURCE / 'app').rglob('*.py'):
        names.update(_ICON_NAME_RE.findall(path.read_text(encoding='utf-8')))
    return names


class TestsGraphIconsShipped(TestCase):

    def test_the_code_should_name_built_in_icons(self):
        self.assertIn('desktop.png', _icons_named_in_the_code())

    def test_every_icon_named_in_the_code_should_be_shipped(self):
        missing = sorted(name for name in _icons_named_in_the_code() if not (_GRAPH_ICONS / name).is_file())
        self.assertEqual([], missing)
