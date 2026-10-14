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

"""The "No proxy for" list of the server settings, as in Firefox.

Entries are separated by commas, spaces or new lines:

  * `*` — every host;
  * `intranet.corp` — that host and its subdomains;
  * `.corp` or `*.corp` — the subdomains only;
  * `10.1.2.3`, `fd00::1` — that IP address;
  * `10.0.0.0/8`, `fd00::/8` — any IP address in the network. Only an
    address written in the URL matches: host names are not resolved;
  * `<local>` — host names without a dot (`jira`, `localhost`);
  * any host or address may end with `:port` (`[fd00::1]:8443` for
    IPv6) to match that port only.

A matching URL is fetched directly, without the server settings proxy
nor the `HTTP(S)_PROXY` environment variables.

`requests` only honours `no_proxy` for proxies it reads from the
environment, never for the ones given to it, hence the matching here.
"""

import ipaddress
import re
from urllib.parse import urlsplit


# What `requests` needs per request to drop the session and environment
# proxies: `None` keys are removed when the settings are merged.
PROXY_BYPASS = {'http': None, 'https': None, 'all': None}

_SEPARATORS = re.compile(r'[\s,;]+')
_HOST_LABEL = re.compile(r'(?!-)[a-z0-9_-]{1,63}(?<!-)')
_DEFAULT_PORTS = {'http': 80, 'https': 443}


def _split_port(entry):
    """(host, port) of an entry; port None when absent."""
    if entry.startswith('['):
        host, bracket, rest = entry[1:].partition(']')
        if not bracket:
            return None, None
        if not rest:
            return host, None
        if not rest.startswith(':'):
            return None, None
        return host, rest[1:]
    if entry.count(':') == 1:
        host, _, port = entry.partition(':')
        return host, port
    # Bare IPv6 address or network: no port
    return entry, None


def _parse_port(port):
    if port is None:
        return None
    if not port.isdigit() or not 0 < int(port) < 65536:
        raise ValueError
    return int(port)


def _is_host_name(name):
    labels = name.split('.')
    return len(name) <= 253 and all(_HOST_LABEL.fullmatch(label) for label in labels)


def _parse_entry(entry):
    """`(kind, value, port)`, or ValueError."""
    if entry == '*':
        return 'any', None, None
    if entry.lower() == '<local>':
        return 'local', None, None
    if '/' in entry and not entry.startswith('['):
        return 'network', ipaddress.ip_network(entry, strict=False), None
    host, port = _split_port(entry)
    if not host:
        raise ValueError
    port = _parse_port(port)
    try:
        return 'address', ipaddress.ip_address(host), port
    except ValueError:
        pass
    host = host.lower().rstrip('.')
    if host.startswith('*.'):
        host = host[1:]
    if host.startswith('.'):
        if not _is_host_name(host[1:]):
            raise ValueError
        return 'subdomains', host, port
    if not _is_host_name(host):
        raise ValueError
    return 'domain', host, port


def _entries(text):
    return [entry for entry in _SEPARATORS.split(text or '') if entry]


def proxy_exceptions_errors(text) -> list:
    """The entries of `text` that are not understood."""
    invalid = []
    for entry in _entries(text):
        try:
            _parse_entry(entry)
        except ValueError:
            invalid.append(entry)
    return invalid


def _url_host_port(url):
    parts = urlsplit(url)
    host = (parts.hostname or '').rstrip('.')
    try:
        port = parts.port or _DEFAULT_PORTS.get(parts.scheme.lower())
    except ValueError:
        port = None
    return host, port


def _matches(rule, host, address, port):
    kind, value, rule_port = rule
    if rule_port is not None and rule_port != port:
        return False
    if kind == 'any':
        return True
    if kind == 'local':
        return address is None and '.' not in host
    if kind == 'network':
        return address is not None and address.version == value.version and address in value
    if kind == 'address':
        return address is not None and address == value
    if address is not None:
        return False
    if kind == 'subdomains':
        return host.endswith(value)
    return host == value or host.endswith(f'.{value}')


def proxy_exceptions_match(url, text) -> bool:
    """Whether `url` is to be fetched without a proxy. Entries that are
    not understood are skipped: the settings refuse them on save."""
    host, port = _url_host_port(url)
    if not host:
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    for entry in _entries(text):
        try:
            rule = _parse_entry(entry)
        except ValueError:
            continue
        if _matches(rule, host, address, port):
            return True
    return False


def proxy_exceptions_request_proxies(url, text):
    """The `proxies` argument of a request to `url`: `PROXY_BYPASS` when
    it is an exception, None to keep the session proxies."""
    if proxy_exceptions_match(url, text):
        return dict(PROXY_BYPASS)
    return None
