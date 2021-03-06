"""
polyanalyst6api.api
~~~~~~~~~~~~~~~~~~~

This module contains functionality for access to PolyAnalyst API.
"""
import contextlib
import warnings
from typing import Any, Dict, List, Tuple, Union, Optional
from urllib.parse import urljoin, urlparse

import requests
import urllib3

from . import __version__
from .drive import Drive
from .project import Parameters, Project
from .exceptions import APIException, ClientException, _WrapperNotFound

__all__ = ['API']

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
warnings.simplefilter('always', UserWarning)  # without this set_parameters will show warnings only once

NodeTypes = [
    "CSV Exporter/",
    "DataSource/CSV",
    "DataSource/EXCEL",
    "DataSource/FILES",
    "DataSource/INET",
    "DataSource/ODBC",
    "DataSource/RSS",
    "DataSource/XML",
    "Dataset/Biased",
    "Dataset/ExtractTerms",
    "Dataset/Python",
    "Dataset/R",
    "Dataset/ReplaceTerms",
    "ODBC Exporter/",
    "PA6TaxonomyResult/TaxonomyResult",
    "SRLRuleSet/Filter Rows",
    "SRLRuleSet/SRL Rule",
    "TmlEntityExtractor/FEX",
    "Sentiment Analysis",
    "TmlLinkTerms/",
]


class API:
    """PolyAnalyst API

    :param url: The scheme, host and port(if exists) of a PolyAnalyst server \
        (e.g. ``https://localhost:5043/``, ``http://example.polyanalyst.com``)
    :param username: The username to login with
    :param password: (optional) The password for specified username
    :param ldap_server: (optional) LDAP Server address
    :param version: (optional) Choose which PolyAnalyst API version to use. Default: ``1.0``

    If ldap_server is provided, then login will be performed via LDAP Server.

    Usage::

      >>> with API(URL, USERNAME, PASSWORD) as api:
      ...     print(api.get_server_info())
    """

    _api_path = '/polyanalyst/api/'
    _valid_api_versions = ['1.0']
    user_agent = f'PolyAnalyst6API python client v{__version__}'

    def __enter__(self) -> 'API':
        self.login()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.logout()
        self._s.__exit__()

    def __init__(
        self,
        url: str,
        username: str,
        password: str = '',
        ldap_server: Optional[str] = None,
        version: str = '1.0',
    ) -> None:
        if version not in self._valid_api_versions:
            raise ClientException('Valid api versions are ' + ', '.join(self._valid_api_versions))

        if not url:
            raise ClientException(f'Invalid url: "{url}".')

        self.base_url = urljoin(url, self._api_path)
        self.url = urljoin(self.base_url, f'v{version}/')
        self.username = username
        self.password = password
        self.ldap_server = ldap_server

        self._s = requests.Session()
        self._s.headers.update({'User-Agent': self.user_agent})
        self.sid = None  # session identity
        # path to certificate file. by default ignore insecure connection warnings
        self.certfile = False
        self.drive = Drive(self)

    @property
    def fs(self):
        warnings.warn('"fs" attribute has been renamed "drive"', DeprecationWarning, 2)
        return self.drive

    def get_versions(self) -> List[str]:
        """Returns api versions supported by PolyAnalyst server."""
        # the 'versions' endpoint was added in the 2191 polyanalyst's version
        try:
            return self.request(urljoin(self.base_url, 'versions'), method='get')[1]
        except APIException:
            return ['1.0']

    def get_server_info(self) -> Optional[Dict[str, Union[int, str, Dict[str, str]]]]:
        """Returns general server information including build number, version and commit hashes."""
        _, data = self.request(urljoin(self.url, 'server/info'), method='get')
        return data

    def get_parameters(self) -> List[Dict[str, Union[str, List]]]:
        """
        Returns list of nodes with parameters supported by ``Parameters`` node.

        .. deprecated:: 0.18.0
            Use :meth:`Parameters.get` instead.
        """
        warnings.warn(
            'API.get_parameters() is deprecated, use Parameters.get() instead.',
            DeprecationWarning,
            stacklevel=2,
        )
        return Parameters(self, None, None).get()

    def login(self) -> None:
        """Logs in to PolyAnalyst Server with user credentials."""
        credentials = {'uname': self.username, 'pwd': self.password}
        if self.ldap_server:
            credentials['useLDAP'] = '1'
            credentials['svr'] = self.ldap_server

        resp, _ = self.request('login', method='post', params=credentials)

        try:
            self.sid = resp.cookies['sid']
        except KeyError:
            self._s.headers['Authorization'] = f"Bearer {resp.headers['x-session-id']}"

    def logout(self) -> None:
        """Logs out current user from PolyAnalyst server."""
        self.get('logout')

    def run_task(self, id: int) -> None:
        """Initiates scheduler task execution.

        :param id: the task ID
        """
        self.post('scheduler/run-task', json={'taskId': id})

    def project(self, uuid: str) -> Project:
        """Returns :class:`Project <Project>` instance with given uuid.

        :param uuid: The project uuid
        """
        prj = Project(self, uuid)
        prj._update_node_list()  # check that the project with given uuid exists
        return prj

    def get(self, endpoint: str, **kwargs) -> Any:
        """Shortcut for GET requests via :meth:`request <API.request>`

        :param endpoint: PolyAnalyst API endpoint
        :param kwargs: :func:`requests.request` keyword arguments
        """
        return self.request(endpoint, method='get', **kwargs)[1]

    def post(self, endpoint: str, **kwargs) -> Any:
        """Shortcut for POST requests via :meth:`request <API.request>`

        :param endpoint: PolyAnalyst API endpoint
        :param kwargs: :func:`requests.request` keyword arguments
        """
        return self.request(endpoint, method='post', **kwargs)[1]

    def request(self, url: str, method: str, **kwargs) -> Tuple[requests.Response, Any]:
        """Sends ``method`` request to ``endpoint`` and returns tuple of
        :class:`requests.Response` and json-encoded content of a response.

        :param url: url or PolyAnalyst API endpoint
        :param method: request method (e.g. GET, POST)
        :param kwargs: :func:`requests.request` keyword arguments
        """
        if not urlparse(url).netloc:
            url = urljoin(self.url, url)
        kwargs['verify'] = self.certfile
        try:
            resp = self._s.request(method, url, **kwargs)
        except requests.RequestException as exc:
            raise ClientException(exc)
        else:
            return self._handle_response(resp)

    @staticmethod
    def _handle_response(response: requests.Response) -> Tuple[requests.Response, Any]:
        try:
            json = response.json()
        except ValueError:
            json = None

        if response.status_code in (200, 202):
            return response, json

        if isinstance(json, dict) and json.get('error'):
            with contextlib.suppress(KeyError):
                error = json['error']
                if 'The wrapper with the given GUID is not found on the server' == error['message']:
                    raise _WrapperNotFound
                error_msg = f"{error['title']}. Message: '{error['message']}'"

        # the old error response format handling
        elif response.status_code == 403:
            if 'are not logged in' in response.text:
                error_msg = 'You are not logged in to PolyAnalyst Server'
            elif 'operation is limited ' in response.text:
                error_msg = 'Access to this operation is limited to project owners and administrator'
        elif response.status_code == 500:
            with contextlib.suppress(IndexError, TypeError):
                if json[0] == 'Error':
                    error_msg = json[1]
        else:
            try:
                response.raise_for_status()
            except requests.HTTPError as exc:
                error_msg = str(exc)

        with contextlib.suppress(NameError):
            raise APIException(error_msg, response.url, response.status_code)

        return response, None
