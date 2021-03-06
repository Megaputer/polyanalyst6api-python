"""
polyanalyst6api.project
~~~~~~~~~~~~~~~~~~~~~~~

This module contains functionality for access to PolyAnalyst Analytical Client API.
"""
import datetime
import functools
import time
import warnings
from urllib.parse import urlparse, parse_qs
from typing import Any, Dict, List, Union, Optional, Tuple, Iterator

from .exceptions import APIException, _WrapperNotFound

__all__ = ['Project', 'Parameters', 'DataSet']

# type hints
Node = Dict[str, Union[str, int]]
Nodes = Dict[str, Dict[str, Union[int, str]]]
_DataSet = List[Dict[str, Any]]
JSON_VAL = Union[bool, str, int, float, None]


class Project:
    """This class maintains all operations with the PolyAnalyst's project and nodes.

    :param api: An instance of :class:`API <API>` class
    :param uuid: The uuid of the project you want to interact with
    """

    def __repr__(self):
        return f'<Project [{self.uuid}]>'

    def __init__(self, api, uuid: str) -> None:
        self.api = api
        self.uuid = uuid
        self._node_list: List[Node] = []

    def get_node_list(self) -> List[Node]:
        """Returns a list of project nodes.

        .. versionadded:: 0.15.0
        """
        return self.api.get(
            'project/nodes',
            params={'prjUUID': self.uuid},
            headers={'sid': self.api.sid} if self.api.sid else None,
        )['nodes']

    def get_execution_stats(self) -> List[Node]:
        """Returns nodes execution statistics.

        .. versionadded:: 0.15.0
        """
        return self.api.get('project/execution-statistics', params={'prjUUID': self.uuid})['nodes']

    def get_tasks(self) -> List[Dict[str, Any]]:
        """Returns task list info."""
        json = self.api.get('project/tasks', params={'prjUUID': self.uuid})
        # convert timestamp in milliseconds to python datetime
        for task in json:
            task['startTime'] = datetime.datetime.utcfromtimestamp(task['startTime'] / 1000)
        return json

    def save(self) -> None:
        """Initiates saving of all changes that have been made in the project."""
        self.api.post('project/save', json={'prjUUID': self.uuid})

    def abort(self) -> None:
        """Aborts the execution of all nodes in the project."""
        self.api.post('project/global-abort', json={'prjUUID': self.uuid})

    def execute(self, *args: Union[str, Dict[str, str]], wait: bool = False) -> Optional[int]:
        """
        Initiates execution of nodes and returns execution wave identifier.

        :param args: node names and/or dicts with name and type of nodes
        :param wait: wait for nodes execution to complete

        Usage::

          >>> wave_id = prj.execute('Internet Source', 'Python')

        use ``wait=True`` to wait for the passed nodes execution to complete.

          >>> prj.execute('Export to MS Word', wait=True)

        or, if there are several nodes in the project with the same name, pass
        them as dicts with `name` and `type` keys (and because of this, you can
        also pass items of :meth:`Project.get_node_list`)

          >>> prj.execute(
          ...     {'name': 'Example node', 'type': 'DataSource'},
          ...     {'name': 'Example node', 'type': 'Dataset'},
          ...     'Federated Search',
          ...     prj.get_node_list()[1],
          ... )
        """
        nodes = []
        for arg in args:
            node = self._find_node(arg)
            nodes.append({'name': node['name'], 'type': node['type']})

        resp, _ = self.api.request(
            'project/execute',
            method='post',
            json={'prjUUID': self.uuid, 'nodes': nodes},
        )

        location = resp.headers.get('location')
        query = urlparse(location).query
        try:
            wave_id = int(parse_qs(query).get('executionWave')[0])
        except TypeError:
            wave_id = None

        if wait:
            if wave_id is None:
                for node in nodes:
                    self.wait_for_completion(node)  # type: ignore
                return

            while self.is_running(wave_id):
                time.sleep(1)

        return wave_id

    def is_running(self, wave_id: int) -> bool:
        """
        Checks that execution wave is still running in the project.

        If `wave_id` is `-1` then the project is checked against any active
        execution, saving, publishing operations.

        :param wave_id: Execution wave identifier
        """
        data = self.api.get(
            'project/is-running',
            params={'prjUUID': self.uuid, 'executionWave': wave_id},
        )
        return bool(data['result'])

    def dataset(self, node: Union[str, Dict[str, str]]):
        """Get dataset wrapper object.

        :param node: node name or dict with name and type of the node

        .. versionadded:: 0.16.0
        """
        return DataSet(self, self._find_node(node))

    def parameters(self, name: str):
        """Get parameters wrapper object.

        :param name: Parameters node name

        .. versionadded:: 0.18.0
        """
        return Parameters(self.api, self.uuid, self._find_node(name)['id'])

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
        self.api.post('project/delete', json={'prjUUID': self.uuid, 'forceUnload': force_unload})

    def _update_node_list(self) -> None:
        self._node_list = self.get_node_list()

    def _find_node(self, node_: Union[str, Dict[str, str]]) -> Node:
        if isinstance(node_, str):
            name_, type_ = node_, None
        else:
            name_, type_ = node_['name'], node_['type']

        for node in self._node_list:
            if node['name'] == name_ and (type_ is None or node['type'] == type_):
                return node

        raise APIException(f"Node not found: name='{name_}', type='{type_}'", status_code=500)

    def wait_for_completion(self, node: Union[str, Dict[str, str]]) -> bool:
        """Waits for the node to complete the execution. Returns True if node have
        completed successfully and False otherwise.

        .. deprecated:: 0.17.0
           Use :meth:`Project.is_running` instead.

        :param node: node name or dict with name and type of node
        """
        warnings.warn(
            'Project.wait_for_completion() is deprecated, use Project.is_running() instead.',
            DeprecationWarning,
            stacklevel=2,
        )
        time.sleep(0.5)  # give pa time to update node statuses
        while True:
            self._update_node_list()
            stats = self._find_node(node)

            if stats.get('errMsg'):
                return False
            if stats['status'] == 'synchronized':
                return True
            if stats['status'] == 'incomplete':
                return False

            time.sleep(1)

    def get_nodes(self) -> Nodes:
        """Returns a dictionary of project's nodes information.

        .. deprecated:: 0.15.0
           Use :meth:`Project.get_node_list` instead.
        """
        warnings.warn(
            'Project.get_nodes() is deprecated, use Project.get_node_list() instead.',
            DeprecationWarning,
            stacklevel=2,
        )
        json = self.api.get(
            'project/nodes',
            params={'prjUUID': self.uuid},
            headers={'sid': self.api.sid} if self.api.sid else None,
        )
        return {node.pop('name'): node for node in json['nodes']}

    def get_execution_statistics(self) -> Tuple[Nodes, Dict[str, int]]:
        """Returns the execution statistics for nodes in the project.

        Similar to :meth:`Project.get_nodes` but nodes contains extra information
        and the project statistics.

        .. deprecated:: 0.15.0
           Use :meth:`Project.get_execution_stats` instead.
        """
        warnings.warn(
            'Project.get_execution_statistics() is deprecated, use Project.get_execution_stats() instead.',
            DeprecationWarning,
            stacklevel=2,
        )
        json = self.api.get('project/execution-statistics', params={'prjUUID': self.uuid})
        nodes = {node.pop('name'): node for node in json['nodes']}
        return nodes, json['nodesStatistics']

    def preview(self, node: Union[str, Dict[str, str]]) -> _DataSet:
        """Returns first 1000 rows of data from ``node``, texts and strings are
        cutoff after 250 symbols.

        :param node: node name or dict with name and type of node

        .. deprecated:: 0.16.0
            Use :meth:`Dataset.preview` instead.
        """
        warnings.warn(
            'Project.preview() is deprecated, use Dataset.preview() instead.',
            DeprecationWarning,
            stacklevel=2,
        )
        return self.dataset(node).preview()

    def set_parameters(
            self,
            node: str,
            node_type: str,
            parameters: Dict[str, Any],
            declare_unsync: bool = True,
            hard_update: bool = True,
    ) -> None:
        """
        Set parameters of the selected Parameters node in the project.

        .. deprecated:: 0.18.0
           Use :meth:`Parameters.set` instead.

        :param node: name of Parameters node
        :param node_type: node type, which parameters need to be set. The types \
            are listed in NodeTypes.
        :param parameters: default parameters of the node to be set.
        :param declare_unsync: reset the status of the Parameters node.
        :param hard_update: update every child node with new parameters if True, \
            otherwise reset their statuses. Works only if declare_unsync is True.
        """
        warnings.warn(
            'Project.set_parameters() is deprecated, use Project.parameters.set() instead.',
            DeprecationWarning,
            stacklevel=2,
        )
        warns = self.parameters(node).set(node_type, parameters, declare_unsync, hard_update)
        if warns:
            for msg in warns:
                warnings.warn(msg)


class Parameters:
    def __init__(self, api, uuid: Optional[str], id: Optional[str]):
        self.api = api
        self.uuid = uuid
        self.id = id

    def get(self):
        """Returns list of nodes with parameters and strategies supported by ``Parameters`` node."""
        return self.api.get('parameters/nodes')

    def set(
            self,
            node_type: str,
            parameters: Union[Dict[str, str], List[Dict[str, str]]],
            strategies: Optional[List[int]] = None,
            declare_unsync: bool = True,
            hard_update: bool = True,
    ) -> Optional[List[str]]:
        """
        Sets `node_type` parameters and strategies for the Parameters node.

        If `parameters` is the list of dictionaries with parameters then
        /configure-array endpoint is used otherwise /configure.

        :param node_type: node type which parameters needs to be set
        :param parameters: node type parameters
        :param strategies: node type strategies
        :param declare_unsync: reset status of the Parameters node. True by default.
        :param hard_update: update every child node with new parameters if True, \
            otherwise reset their statuses. Works only if declare_unsync is True.\
            True by default.
        """

        if strategies is None:
            strategies = []

        method = 'configure-array' if isinstance(parameters, list) else 'configure'

        return self.api.post(
            f'parameters/{method}',
            params={'prjUUID': self.uuid, 'obj': self.id},
            json={
                'type': node_type,
                'settings': parameters,
                'strategies': strategies,
                'declareUnsync': declare_unsync,
                'hardUpdate': hard_update,
            },
        )

    def clear(self, *node_types: List[str], declare_unsync: bool = True) -> Optional[List[str]]:
        """
        Clears parameters and strategies of `node_types` for the Parameters node.
        If `node_types` is empty it clears parameters and strategies of all nodes.

        :param node_types: node types which parameters needs to be cleared
        :param declare_unsync: reset status of the Parameters node
        """
        return self.api.post(
            'parameters/clear',
            params={'prjUUID': self.uuid, 'obj': self.id},
            json={
                'nodes': node_types,
                'declareUnsync': declare_unsync,
            }
        )


def retry_on_invalid_guid(func):
    @functools.wraps(func)
    def wrapper(cls, *args, **kwargs):
        try:
            return func(cls, *args, **kwargs)
        except _WrapperNotFound:
            cls._update_guid()
            return func(cls, *args, **kwargs)
    return wrapper


class DataSet:
    def __init__(self, prj: Project, node: Node):
        self._prj = prj
        self._api = prj.api
        self._node = node
        # on purpose send wrong wrapperGuid(empty string) at first request to /dataset/* endpoints
        # to create dataset wrapper on server and retrieve its' guid by @retry_on_invalid_guid
        self.guid: str = ''

    @retry_on_invalid_guid
    def get_info(self) -> Dict[str, Any]:
        """Get information about dataset."""
        return self._api.get('dataset/info', params={'wrapperGuid': self.guid})

    @retry_on_invalid_guid
    def get_progress(self) -> Dict[str, Union[int, str]]:
        """Get dataset progress."""
        return self._api.get('dataset/progress', params={'wrapperGuid': self.guid})

    def preview(self) -> _DataSet:
        """Returns first 1000 rows with strings truncated to 250 characters."""
        return self._api.get(
            'dataset/preview',
            params={'prjUUID': self._prj.uuid, 'name': self._node['name'], 'type': self._node['type']},
        )

    def iter_rows(self, start: int = 0, stop: Optional[int] = None) -> Iterator[Dict[str, JSON_VAL]]:
        """
        Iterate over rows in dataset.

        :param start:
        :param stop:

        :raises: ValueError if `start` or `stop` is out of datasets' row range

        Usage::

          # download first 10 rows
          >>> head = []
          >>> for row in ds.iter_rows(0, 10):
          ...     head.append(row)
          # download full dataset and convert it to pandas.DataFrame
          >>> table = list(ds.iter_rows())
          >>> df = pandas.DataFrame(table)
        """
        info = self.get_info()
        max_row = info['rowCount']
        if stop is None:
            stop = max_row

        # предпологается что если stop определен то пользователь в курсе количества строк в датасете
        if not 0 <= start <= stop <= max_row:
            raise ValueError(f'start and stop arguments must be within dataset row range: (0, {max_row})')

        rows = self._values(stop)['table']
        get_text = self._cell_text

        class RowIterator:
            def __init__(self):
                self.idx = start

            def __iter__(self):
                return self

            def __next__(self):
                if self.idx >= stop:
                    raise StopIteration

                result = {}
                for column in info['columnsInfo']:
                    if column['flags'].get('getTextAlways'):
                        result[column['title']] = get_text(self.idx, column['id'], column['title'])
                    # elif column['type'] == 'DateTime':  # todo convert to python datetime?
                    else:
                        result[column['title']] = rows[self.idx][column['id']]

                self.idx += 1
                return result

        return RowIterator()

    def _update_guid(self) -> None:
        self.guid = self._api.get(
            'dataset/wrapper-guid',
            params={'prjUUID': self._prj.uuid, 'obj': self._node['id']},
        )['wrapperGuid']

    @retry_on_invalid_guid
    def _values(self, row_count: int) -> Dict[str, Union[List, Dict]]:
        return self._api.get('dataset/values', json={'wrapperGuid': self.guid, 'rowCount': row_count})

    @retry_on_invalid_guid
    def _cell_text(self, row: int, col: int, _title) -> str:
        return self._api.get(
            'dataset/cell-text',
            json={
                'wrapperGuid': self.guid,
                'row': row,
                'col': col,
                # todo remove next keys
                'colTitle': _title,
                'offset': 0,
                'count': 0,
            },
        )['text']
