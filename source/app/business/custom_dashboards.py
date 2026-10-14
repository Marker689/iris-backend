#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Business-layer re-exports for custom dashboards.

Keeps `app.blueprints.rest.v2.custom_dashboards` off `app.datamgmt`
directly so the layering contract passes.
"""

from app.datamgmt.custom_dashboard.custom_dashboard_db import (
    DashboardAccessError,
    DashboardNotFoundError,
    DashboardSystemReadOnlyError,
    create_dashboard_for_user,
    delete_dashboard_for_user,
    get_dashboard_for_user,
    list_dashboards_for_user,
    serialize_dashboard,
    update_dashboard_for_user,
)
from app.datamgmt.custom_dashboard.named_aggregations import (
    ComputedFilters,
    NamedAggregationError,
    compute_named_aggregation,
    list_named_aggregations,
)
from app.datamgmt.custom_dashboard.query_engine import (
    QueryExecutionError,
    WidgetQueryExecutor,
    format_widget_payload,
)
from app.datamgmt.custom_dashboard.schema import CustomDashboardSchema
from app.datamgmt.custom_dashboard.schema import DASHBOARD_FILTER_OPERATORS


__all__ = [
    'ComputedFilters',
    'CustomDashboardSchema',
    'DASHBOARD_FILTER_OPERATORS',
    'DashboardAccessError',
    'DashboardNotFoundError',
    'DashboardSystemReadOnlyError',
    'NamedAggregationError',
    'QueryExecutionError',
    'WidgetQueryExecutor',
    'compute_named_aggregation',
    'create_dashboard_for_user',
    'delete_dashboard_for_user',
    'format_widget_payload',
    'get_dashboard_for_user',
    'list_dashboards_for_user',
    'list_named_aggregations',
    'serialize_dashboard',
    'update_dashboard_for_user',
]
