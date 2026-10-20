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

"""Migration c3e8f1a4d726 (op / schema probes mocked)."""

import importlib.util
from pathlib import Path
from unittest import TestCase
from unittest.mock import MagicMock
from unittest.mock import patch

from app.models.alerts import SimilarAlertsCache
from app.models.authorization import UserCaseEffectiveAccess


_PATH = (Path(__file__).resolve().parents[2] / 'app' / 'alembic' / 'versions'
         / 'c3e8f1a4d726_lookup_indexes_for_access_and_similar_alerts.py')


def _load_migration():
    spec = importlib.util.spec_from_file_location('_migration_c3e8f1a4d726', _PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _model_indexes():
    """`{name: (table, columns, using)}` as `db.create_all()` would build them."""
    indexes = {}
    for model in (UserCaseEffectiveAccess, SimilarAlertsCache):
        for index in model.__table__.indexes:
            indexes[index.name] = (
                model.__tablename__,
                [column.name for column in index.columns],
                index.dialect_options['postgresql']['using'] or None
            )
    return indexes


class TestLookupIndexesMigration(TestCase):

    def setUp(self):
        self.migration = _load_migration()

    def test_upgrade_should_create_the_indexes_the_models_declare(self):
        # A fresh install gets its indexes from the models and an upgraded
        # one from this migration: they have to be the same indexes.
        op = MagicMock()
        with patch.object(self.migration, 'op', op), \
                patch.object(self.migration, '_has_table', return_value=True), \
                patch.object(self.migration, 'index_exists', return_value=False):
            self.migration.upgrade()

        created = {}
        for call in op.create_index.call_args_list:
            name, table, columns = call.args
            created[name] = (table, columns, call.kwargs.get('postgresql_using'))
        self.assertEqual(_model_indexes(), created)

    def test_no_index_should_be_unique(self):
        for model in (UserCaseEffectiveAccess, SimilarAlertsCache):
            for index in model.__table__.indexes:
                self.assertFalse(index.unique, index.name)

    def test_upgrade_should_skip_existing_indexes(self):
        op = MagicMock()
        with patch.object(self.migration, 'op', op), \
                patch.object(self.migration, '_has_table', return_value=True), \
                patch.object(self.migration, 'index_exists', return_value=True):
            self.migration.upgrade()
        op.create_index.assert_not_called()

    def test_upgrade_should_skip_missing_tables(self):
        op = MagicMock()
        with patch.object(self.migration, 'op', op), \
                patch.object(self.migration, '_has_table', return_value=False), \
                patch.object(self.migration, 'index_exists', return_value=False):
            self.migration.upgrade()
        op.create_index.assert_not_called()

    def test_downgrade_should_drop_every_index(self):
        op = MagicMock()
        with patch.object(self.migration, 'op', op), \
                patch.object(self.migration, '_has_table', return_value=True), \
                patch.object(self.migration, 'index_exists', return_value=True):
            self.migration.downgrade()
        self.assertEqual(
            set(_model_indexes()),
            {call.args[0] for call in op.drop_index.call_args_list}
        )
