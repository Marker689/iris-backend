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

"""Who may change the access of a case, and to whom.

Shared by the `/api/v2/cases/<id>/access/*` routes and the case access
MCP tools, so both enforce the same rules for the current user.
"""

from app.blueprints.access_controls import ac_current_user_has_permission
from app.blueprints.access_controls import ac_fast_check_current_user_has_case_access
from app.blueprints.iris_user import iris_current_user
from app.business.cases_access import cases_access_user_sees_customer
from app.models.authorization import CaseAccessLevel
from app.models.authorization import Group
from app.models.authorization import Permissions
from app.models.authorization import User
from app.models.cases import Cases
from app.models.errors import BusinessProcessingError


def cases_access_guards_can_manage(case_identifier) -> bool:
    """Whether the current user may change who can access this case.

    Server administrators always may. Anyone else needs `case_access_manage`
    *and* full access to this very case: the permission lets them share
    cases they work on, never reach into ones they don't.
    """
    if ac_current_user_has_permission(Permissions.server_administrator):
        return True
    if not ac_current_user_has_permission(Permissions.case_access_manage):
        return False
    return ac_fast_check_current_user_has_case_access(case_identifier, [CaseAccessLevel.full_access]) is not None


def cases_access_guards_check_user(case: Cases, user: User):
    """Raise `BusinessProcessingError` if the current user may not set `user`'s access.

    Nobody changes their own access — it is the one change that can leave
    a case with no one able to fix it. A non-administrator may only grant
    users who can see the case's customer, as for the owner and reviewer
    of a case.
    """
    if user.id == iris_current_user.id:
        raise BusinessProcessingError('You cannot change your own access to a case')
    if not ac_current_user_has_permission(Permissions.server_administrator) \
            and not cases_access_user_sees_customer(user.id, case.client_id):
        raise BusinessProcessingError('This user has no access to the customer of the case')


def cases_access_guards_check_group(case: Cases, group: Group, access_level):
    """Raise `BusinessProcessingError` if the current user may not set `group`'s access to `access_level`.

    `group.group_members` must be loaded (`cases_access_get_group`). For a
    non-administrator the per-user guards apply to every member: no
    lowering a group they belong to below full access, and every member
    must be able to see the case's customer.
    """
    if ac_current_user_has_permission(Permissions.server_administrator):
        return
    member_identifiers = [member['id'] for member in group.group_members]
    if iris_current_user.id in member_identifiers and access_level != CaseAccessLevel.full_access.value:
        raise BusinessProcessingError('You cannot lower the access of a group you belong to')
    if any(not cases_access_user_sees_customer(member_identifier, case.client_id)
           for member_identifier in member_identifiers):
        raise BusinessProcessingError('Some members of this group have no access to the customer of the case')
