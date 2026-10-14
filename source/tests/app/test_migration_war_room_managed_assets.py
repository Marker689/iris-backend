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

"""Migration 0c792729f4a3 (op / schema probes mocked)."""

import importlib.util
from pathlib import Path
from unittest import TestCase
from unittest.mock import MagicMock
from unittest.mock import patch

from app.models.war_rooms import WarRoomManagedAsset


_PATH = Path(__file__).resolve().parents[2] / 'app' / 'alembic' / 'versions' / '0c792729f4a3_war_room_managed_assets.py'


def _load_migration():
    spec = importlib.util.spec_from_file_location('_migration_0c792729f4a3', _PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestWarRoomManagedAssetsMigration(TestCase):

    def setUp(self):
        self.migration = _load_migration()

    def test_upgrade_should_create_the_table_and_its_indexes(self):
        op = MagicMock()
        with patch.object(self.migration, 'op', op), \
                patch.object(self.migration, '_has_table', return_value=False):
            self.migration.upgrade()
        op.create_table.assert_called_once()
        self.assertEqual(op.create_table.call_args.args[0], WarRoomManagedAsset.__tablename__)
        model_indexes = {index.name for index in WarRoomManagedAsset.__table__.indexes}
        self.assertEqual({call.args[0] for call in op.create_index.call_args_list}, model_indexes)

    def test_upgrade_should_skip_an_existing_table(self):
        op = MagicMock()
        with patch.object(self.migration, 'op', op), \
                patch.object(self.migration, '_has_table', return_value=True):
            self.migration.upgrade()
        op.create_table.assert_not_called()
        op.create_index.assert_not_called()

    def test_downgrade_should_drop_the_table(self):
        op = MagicMock()
        with patch.object(self.migration, 'op', op), \
                patch.object(self.migration, '_has_table', return_value=True):
            self.migration.downgrade()
        op.drop_table.assert_called_once_with('war_room_managed_asset')
