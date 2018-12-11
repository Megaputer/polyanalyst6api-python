"""
polyanalyst6api.api
~~~~~~~~~~~~~~~~~~~

This module contains functionality for access to PolyAnalyst API.
"""

import contextlib
import time
from urllib.parse import urljoin
from typing import Any, Dict, List, Tuple, Union

import requests

from . import __version__
from .exceptions import *

__all__ = ['API', 'Project', 'PAException', 'ClientException', 'APIException']

_API_PATH = '/polyanalyst/api/'
_VALID_API_VERSIONS = ['1.0']

# typing
_Response = Tuple[requests.Response, Any]
_Nodes = Dict[str, Dict[str, Union[int, str]]]
_DataSet = List[Dict[str, Any]]


class API:
    """PolyAnalyst API

    :param url: The scheme, host and port(if exists) of a PolyAnalyst server
        (e.g. ``https://localhost:5043/``, ``http://example.polyanalyst.com`` .etc)
    :param username: The username to login with
    :param password: (optional) The password for specified username
    :param version: (optional) Choose which PolyAnalyst API version to use.
        Default: ``1.0``

    Usage::

      >>> import polyanalyst6api
      >>> api = polyanalyst6api.API(URL, USERNAME, PASSWORD)
      >>> api.login()

    Or as a context manager::

      >>> with polyanalyst6api.API(URL, USERNAME, PASSWORD) as api:
      >>>     pass
    """

    user_agent = f'PolyAnalyst6API python client v{__version__}'

    def __enter__(self) -> 'API':
        self.login()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._s.__exit__()

    def __init__(
        self,
        url: str,
        username: str,
        password: str = '',
        version: str = '1.0',
    ) -> None:
        if version not in _VALID_API_VERSIONS:
            raise ClientException('Valid api versions are ' + ', '.join(_VALID_API_VERSIONS))

        self.base_url = f'{urljoin(url, _API_PATH)}v{version}/'
        self.username = username
        self.password = password

        self._s = requests.Session()
        self.sid = ''  # session identity
        # path to certificate file. by default ignore insecure connection warnings
        self.certfile = False

    def login(self) -> None:
        """Logs in to PolyAnalyst Server with user credentials."""
        resp, _ = self.request(
            'login',
            method='post',
            params={'uname': self.username, 'pwd': self.password},
        )
        self.sid = resp.cookies['sid']

    def run_task(self, id: int) -> None:
        """Initiates scheduler task execution.

        :param id: the task ID
        """
        self.post('scheduler/run-task', json={'taskId': id})

    def project(self, uuid: str) -> 'Project':
        """Checks project with given uuid on existence and returns
        :class:`Project` instance.

        :param uuid: The project uuid
        """
        prj = Project(self, uuid)
        _ = prj.nodes
        return prj

    def get(self, endpoint: str, **kwargs) -> Any:
        """Shortcut for GET requests via :func:`~api.API.request`

        :param endpoint: PolyAnalyst API endpoint
        :param kwargs: :func:`requests.request` keyword arguments
        """
        return self.request(endpoint, method='get', **kwargs)[1]

    def post(self, endpoint: str, **kwargs) -> Any:
        """Shortcut for POST requests via :func:`~api.API.request`

        :param endpoint: PolyAnalyst API endpoint
        :param kwargs: :func:`requests.request` keyword arguments
        """
        return self.request(endpoint, method='post', **kwargs)[1]

    def request(self, endpoint: str, method: str, **kwargs) -> _Response:
        """Sends ``method`` request to ``endpoint`` and returns tuple of
        :class:`requests.Response` and json.

        :param endpoint: PolyAnalyst API endpoint
        :param method: request method (e.g. GET, POST)
        :param kwargs: :func:`requests.request` keyword arguments
        """
        url = urljoin(self.base_url, endpoint)
        kwargs['verify'] = self.certfile
        try:
            resp = self._s.request(method, url, **kwargs)
        except requests.RequestException as e:
            raise ClientException(e)
        else:
            return self._handle_response(resp)

    @staticmethod
    def _handle_response(response: requests.Response) -> _Response:
        try:
            json = response.json()
        except ValueError:
            json = None

        if response.status_code in (200, 202):
            return response, json

        if response.status_code == 403:
            if 'are not logged in' in response.text:
                error_msg = 'You are not logged in to PolyAnalyst Server'
            elif 'operation is limited ' in response.text:
                error_msg = ('Access to this operation is limited to project '
                             'owners and administrator')
        elif response.status_code == 500:
            try:
                if json[0] != 'Error':
                    raise Exception
                error_msg = json[1]
            except Exception:
                pass
        else:
            try:
                response.raise_for_status()
            except requests.HTTPError as e:
                error_msg = e

        with contextlib.suppress(NameError):
            raise APIException(error_msg, response.url, response.status_code)

        raise ClientException('An error occurred processing your request')


class Project:
    """This class maintains all operations with the PolyAnalyst's project and nodes.

    :param api: An instance of API class
    :param uuid: The uuid of the project you want to interact with
    """

    def __repr__(self):
        return f'Project({self.uuid})'

    def __init__(self, api: API, uuid: str) -> None:
        self.api = api
        self.uuid = uuid
        self._nodes: _Nodes = {}

    @property
    def nodes(self) -> _Nodes:
        """Returns a dictionary of project's nodes information.

        The node value is a dict with a mandatory keys: id, type, status.
        It also may contain errMsg key if last node execution was failed.
        """
        json = self.api.get(
            'project/nodes',
            params={'prjUUID': self.uuid},
            headers={'sid': self.api.sid},
        )
        self._nodes = {node.pop('name'): node for node in json['nodes']}
        return self._nodes

    @property
    def execution_statistics(self) -> Tuple[_Nodes, Dict[str, int]]:
        """Returns the execution statistics for nodes in the project.

        Similar to :func:`~api.Project.nodes` but nodes contains extra
            information and the project statistics.
        """
        json = self.api.get(
            'project/execution-statistics',
            params={'prjUUID': self.uuid}
        )
        nodes = {node.pop('name'): node for node in json['nodes']}
        return nodes, json['nodesStatistics']

    def save(self) -> None:
        """Initiates saving of all changes that have been mode in the project."""
        self.api.post('project/save', json={'prjUUID': self.uuid})

    def abort(self) -> None:
        """Aborts the execution of all nodes in the project."""
        self.api.post('project/global-abort', json={'prjUUID': self.uuid})

    def execute(self, *nodes: str) -> None:
        """Initiate execution of nodes and their children.

        :param nodes: The node names

        Usage::

          >>> prj.execute('Python')

        Execute all nodes in project(assuming there's no connection between them)::

          >>> prj.execute(*prj.nodes)

        """
        self.api.post(
            'project/execute',
            json={
                'prjUUID': self.uuid,
                'nodes': [{'type': self._nodes[name]['type'], 'name': name} for name in nodes],
            },
        )

    def preview(self, node: str) -> _DataSet:
        """Returns first 1000 rows of data from ``node``, texts and strings are
        cutoff after 250 symbols.

        :param node: The node name
        """
        return self.api.get(
            'dataset/preview',
            params={
                'prjUUID': self.uuid,
                'name': node,
                'type': self._nodes[node]['type'],
            },
        )

    def unload(self) -> None:
        """Unload the project from the memory and free system resources."""
        self.api.post('project/unload', json={'prjUUID': self.uuid})

    def repair(self) -> None:
        """Initiate the project repairing operation."""
        self.api.post('project/repair', json={'prjUUID': self.uuid})

    def delete(self, force_unload: bool = False) -> None:
        """Delete the project from server.

        :param force_unload: Delete project regardless other users

        By default the project will be deleted only if it's not loaded to memory.
        To delete the project that loaded to memory (there are users working on
        this project right now) set ``force_unload`` to ``True``.
        This operation available only for project owner and administrators, and
        cannot be undone.
        """
        self.api.post(
            'project/delete',
            json={'prjUUID': self.uuid, 'forceUnload': force_unload}
        )

    def wait_for_completion(self, node: str) -> bool:
        """Waits for the node to complete the execution, returns False if node
        failed at execution or True otherwise.

        :param node: The node name
        """
        while True:
            stats = self.nodes[node]
            if stats.get('errMsg'):
                return False
            if stats['status'] == 'synchronized':
                return True
            time.sleep(1.)