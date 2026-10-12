#  IRIS Source Code
#  Copyright (C) 2023 - DFIR-IRIS
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

import requests
from requests.exceptions import ConnectionError
from requests.exceptions import JSONDecodeError
from requests.exceptions import Timeout
from urllib import parse

# Seconds; a stalled request fails its test instead of hanging the whole run
_TIMEOUT = 120


class RestApi:

    def __init__(self, url, api_key):
        self._url = url
        self._api_key = api_key
        self._headers = {'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'}

    def _build_url(self, path):
        return parse.urljoin(self._url, path)

    @staticmethod
    def _convert_response_to_string(response):
        try:
            return f'{response.status_code} {response.json()}'
        except JSONDecodeError:
            return f'{response.status_code}'

    def post(self, path, payload, query_parameters=None):
        url = self._build_url(path)
        response = requests.post(url, headers=self._headers, params=query_parameters, json=payload, timeout=_TIMEOUT)
        response_as_string = self._convert_response_to_string(response)
        print(f'POST {url} {payload} => {response_as_string}')
        return response

    def get(self, path, query_parameters=None):
        url = self._build_url(path)
        response = requests.get(url, headers=self._headers, params=query_parameters, timeout=_TIMEOUT)
        response_as_string = self._convert_response_to_string(response)
        print(f'GET {url} => {response_as_string}')
        return response

    def put(self, path, payload):
        url = self._build_url(path)
        response = requests.put(url, headers=self._headers, json=payload, timeout=_TIMEOUT)
        response_as_string = self._convert_response_to_string(response)
        print(f'PUT {url} {payload} => {response_as_string}')
        return response

    def patch(self, path, payload):
        url = self._build_url(path)
        response = requests.patch(url, headers=self._headers, json=payload, timeout=_TIMEOUT)
        response_as_string = self._convert_response_to_string(response)
        print(f'PATCH {url} {payload} => {response_as_string}')
        return response

    def delete(self, path, query_parameters=None):
        url = self._build_url(path)
        response = requests.delete(url, headers=self._headers, params=query_parameters, timeout=_TIMEOUT)
        response_as_string = self._convert_response_to_string(response)
        print(f'DELETE {url} => {response_as_string}')
        return response

    def is_ready(self):
        try:
            requests.head(self._url, timeout=_TIMEOUT)
            return True
        except (ConnectionError, Timeout):
            return False

    def post_multipart_encoded_files(self, path, data, files):
        headers = {'Authorization': f'Bearer {self._api_key}'}
        url = self._build_url(path)
        response = requests.post(url, headers=headers, data=data, files=files, timeout=_TIMEOUT)
        response_as_string = self._convert_response_to_string(response)
        print(f'POST {url} {data} {list(files)} => {response_as_string}')
        return response
