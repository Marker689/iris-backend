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

"""MCP tool classification + authorization wiring. No DB.

Two things are pinned:
  * exhaustiveness: a new MCP tool that nobody classified fails here
    rather than at analyst-facing runtime (that's the safety hinge for
    the confirm-writes UX);
  * the access level each tool resolves to: a mutating tool must
    demand `full_access` on its case / war room, matching the REST route
    that performs the same change.
"""

from unittest import TestCase

from app.blueprints.rest.v2.mcp.classification import READ_ONLY_TOOLS
from app.blueprints.rest.v2.mcp.classification import WRITE_TOOLS
from app.blueprints.rest.v2.mcp.classification import mutates
from app.blueprints.rest.v2.mcp.dispatch import required_case_levels
from app.blueprints.rest.v2.mcp.dispatch import required_war_room_levels
from app.blueprints.rest.v2.mcp.registry import TOOL_REGISTRY
from app.models.authorization import CaseAccessLevel
from app.models.authorization import Permissions
from app.models.authorization import WarRoomAccessLevel


class TestsMCPToolAuthorization(TestCase):

    def test_read_and_write_sets_are_disjoint(self):
        overlap = READ_ONLY_TOOLS & WRITE_TOOLS
        self.assertEqual(set(), overlap,
                         f'A tool cannot be both read and write: {overlap}')

    def test_every_tool_runs_the_function_of_its_name(self):
        """`@mcp_tool` registers whatever def follows it: a helper slipped
        in between would be dispatched in place of the tool."""
        mismatched = {name: spec.handler.__name__ for name, spec in TOOL_REGISTRY.items()
                      if spec.handler.__name__ != name}
        self.assertEqual({}, mismatched)

    def test_every_mvp_tool_is_classified(self):
        """Every MVP-enabled MCP tool must appear in either
        READ_ONLY_TOOLS or WRITE_TOOLS. An unclassified tool is refused
        by the chatbot and gated on full_access by dispatch — the CI
        failure here catches it at merge time instead."""
        classified = READ_ONLY_TOOLS | WRITE_TOOLS
        mvp_tools = {name for name, spec in TOOL_REGISTRY.items()
                     if getattr(spec, 'mvp', False)}
        unclassified = mvp_tools - classified
        self.assertEqual(
            set(), unclassified,
            f'MVP tools missing classification: {sorted(unclassified)}. '
            f'Add each name to either READ_ONLY_TOOLS or WRITE_TOOLS in '
            f'app/blueprints/rest/v2/mcp/classification.py.',
        )

    def test_classification_covers_registered_writes(self):
        """A tool present in WRITE_TOOLS that isn't registered in
        TOOL_REGISTRY is dead code (or a typo). Reverse-check catches
        both."""
        registered = set(TOOL_REGISTRY.keys())
        stale = (READ_ONLY_TOOLS | WRITE_TOOLS) - registered
        self.assertEqual(
            set(), stale,
            f'Classification lists reference unregistered tool(s): {sorted(stale)}. '
            f'Remove them from classification.py.',
        )

    def test_unknown_tool_counts_as_mutating(self):
        """Fail-closed: anything not explicitly read-only is a write, so
        a tool added without classification gets the strict gate."""
        self.assertTrue(mutates('iris_not_a_real_tool'))
        self.assertTrue(mutates('iris_case_notes_create'))
        self.assertFalse(mutates('iris_case_notes_list'))

    def test_write_tools_require_full_access(self):
        """Every mutating case-scoped tool resolves to full_access only —
        never read_only. This is the check that stops a read-only case
        member from driving a write through the LLM."""
        for name in sorted(WRITE_TOOLS):
            spec = TOOL_REGISTRY[name]
            if spec.case_scoped:
                self.assertEqual(
                    [CaseAccessLevel.full_access], required_case_levels(name),
                    f'{name} is a write tool but accepts read_only case access',
                )
            if spec.war_room_scoped:
                self.assertEqual(
                    [WarRoomAccessLevel.full_access], required_war_room_levels(name),
                    f'{name} is a write tool but accepts read_only war-room access',
                )

    def test_read_tools_accept_read_only_access(self):
        """The converse — a read tool must not demand full_access, or a
        read-only case member loses the ability to ask about their own
        case."""
        for name in sorted(READ_ONLY_TOOLS):
            self.assertIn(CaseAccessLevel.read_only, required_case_levels(name))
            self.assertIn(WarRoomAccessLevel.read_only,
                          required_war_room_levels(name))

    def test_war_room_tools_declare_war_room_permissions(self):
        """War-room tools mirror `require_war_room_read` /
        `require_war_room_write`: the matching permission, or
        server_administrator. `standard_user` would let any authenticated
        account in."""
        war_room_tools = {
            name for name, spec in TOOL_REGISTRY.items()
            if spec.war_room_scoped or name.startswith('iris_war_room')
        }
        self.assertTrue(war_room_tools, 'no war-room tools registered')

        for name in sorted(war_room_tools):
            spec = TOOL_REGISTRY[name]
            expected = (Permissions.war_rooms_write if mutates(name)
                        else Permissions.war_rooms_read)
            self.assertIn(
                expected, spec.permissions,
                f'{name} should declare {expected.name}, got '
                f'{[p.name for p in spec.permissions]}',
            )
            self.assertIn(Permissions.server_administrator, spec.permissions,
                          f'{name} should also admit server administrators')
