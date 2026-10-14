#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""MCP tools for the Customers domain."""
from __future__ import annotations

from app.blueprints.access_controls import ac_current_user_has_customer_access
from app.blueprints.access_controls import ac_current_user_has_permission
from app.blueprints.access_controls import ac_fast_check_current_user_has_case_access
from app.blueprints.iris_user import iris_current_user
from app.blueprints.rest.v2.mcp import protocol
from app.blueprints.rest.v2.mcp.dispatch import MCPError
from app.blueprints.rest.v2.mcp.registry import mcp_tool
from app.business.cases import cases_get_by_identifier
from app.business.customers import customers_filter
from app.business.customers import customers_get
from app.business.customers_contacts import customers_contacts_list
from app.models.authorization import CaseAccessLevel
from app.models.authorization import Permissions
from app.models.errors import ObjectNotFoundError
from app.models.pagination_parameters import PaginationParameters
from app.schema.marshables import ContactSchema
from app.schema.marshables import CustomerSchema


_customer_schema = CustomerSchema()
_contact_schema = ContactSchema()

_LIST_PAGE_SIZE = 100


def _case_customer_identifier(case_identifier: int) -> int:
    # Same answer for a missing case and a forbidden one
    denied = MCPError(protocol.IRIS_ACCESS_DENIED, f'No access to case #{case_identifier}.')
    if not ac_fast_check_current_user_has_case_access(
            case_identifier, [CaseAccessLevel.read_only, CaseAccessLevel.full_access]):
        raise denied
    try:
        return cases_get_by_identifier(case_identifier).client_id
    except ObjectNotFoundError as exc:
        raise denied from exc


@mcp_tool(
    name='iris_customers_get',
    description=(
        'Fetch a customer: name, description, SLA, custom attributes, contacts and its key/values '
        '(free per-customer settings such as a tenant ID, under `customer_key_values`). Give either '
        'customer_id, or case_identifier to read the customer the case belongs to.'
    ),
    input_schema={
        'type': 'object',
        'properties': {
            'customer_id': {
                'type': 'integer',
                'description': 'Customer to read.',
            },
            'case_identifier': {
                'type': 'integer',
                'description': 'Read the customer of this case instead.',
            },
        },
    },
    permissions=(Permissions.customers_read,),
)
def iris_customers_get(args: dict) -> dict:
    customer_identifier = args.get('customer_id')
    case_identifier = args.get('case_identifier')
    if customer_identifier is None and case_identifier is None:
        raise MCPError(protocol.INVALID_PARAMS, 'Give customer_id or case_identifier.')
    if case_identifier is not None:
        case_customer = _case_customer_identifier(case_identifier)
        if customer_identifier is not None and customer_identifier != case_customer:
            raise MCPError(protocol.INVALID_PARAMS,
                           f'Case #{case_identifier} does not belong to customer #{customer_identifier}.')
        # Reading the case grants reading who it is for
        customer_identifier = case_customer
    elif not ac_current_user_has_customer_access(customer_identifier):
        raise MCPError(protocol.IRIS_ACCESS_DENIED, f'Not entitled to customer #{customer_identifier}.')
    try:
        customer = customers_get(customer_identifier)
    except ObjectNotFoundError as exc:
        raise MCPError(protocol.INVALID_PARAMS, 'Customer not found.') from exc
    result = _customer_schema.dump(customer)
    result['customer_key_values'] = result.get('customer_key_values') or {}
    result['contacts'] = _contact_schema.dump(customers_contacts_list(customer_identifier), many=True)
    return result


@mcp_tool(
    name='iris_customers_list',
    description=(
        'List the customers you can access: id, name, description and key/values (free per-customer '
        'settings such as a tenant ID, under `customer_key_values`). Use it to find the customer of '
        'an external tenant by name or by key/value. `search` is a case-insensitive substring of the '
        'name or description; `customer_ids` restricts the list to these customers. 100 a page, '
        '`has_more` tells whether `page` + 1 has more.'
    ),
    input_schema={
        'type': 'object',
        'properties': {
            'search': {
                'type': 'string',
                'description': 'Substring of the name or description, case-insensitive.',
            },
            'customer_ids': {
                'type': 'array',
                'items': {'type': 'integer'},
                'description': 'Only these customers.',
            },
            'page': {
                'type': 'integer',
                'minimum': 1,
                'description': 'Page, from 1 (default 1).',
            },
        },
    },
    permissions=(Permissions.customers_read,),
)
def iris_customers_list(args: dict) -> dict:
    search = (args.get('search') or '').strip() or None
    pagination = PaginationParameters(args.get('page') or 1, _LIST_PAGE_SIZE, 'client_id', 'asc')
    customers = customers_filter(iris_current_user, pagination,
                                 ac_current_user_has_permission(Permissions.server_administrator),
                                 search=search, identifiers=args.get('customer_ids'))
    return {
        'customers': [_customer_summary(customer) for customer in customers.items],
        'page': customers.page,
        'has_more': customers.has_next,
    }


def _customer_summary(customer) -> dict:
    return {
        'customer_id': customer.client_id,
        'customer_name': customer.name,
        'customer_description': customer.description,
        'customer_key_values': customer.key_values or {},
    }
