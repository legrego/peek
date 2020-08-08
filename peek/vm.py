import ast
import json
import logging
import os
import sys

from peek.ast import Visitor, EsApiCallNode, DictNode, KeyValueNode, ArrayNode, NumberNode, \
    StringNode, Node, FuncCallNode, NameNode, TextNode
from peek.errors import PeekError
from peek.names import NAMES
from peek.visitors import Ref

_logger = logging.getLogger(__name__)


class PeekVM(Visitor):

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.names = {}
        self.builtin_names = NAMES
        self._load_extensions()
        self.es_api_payload_line = []
        self.func_args = []
        self.func_kwargs = {}

    def execute_node(self, node: Node):
        node.accept(self)

    def visit_es_api_call_node(self, node: EsApiCallNode):
        options = Ref()
        self.push_consumer(lambda v: options.set(v))
        self._do_visit_dict_node(node.options_node)
        self.pop_consumer()
        options = options.get()

        dicts = []
        self.push_consumer(lambda v: dicts.append(v))
        for dict_node in node.dict_nodes:
            dict_node.accept(self)
        self.pop_consumer()

        lines = [json.dumps(d) for d in dicts]
        payload = ('\n'.join(lines) + '\n') if lines else None
        try:
            headers = {}
            if options.get('runas') is not None:
                headers['es-security-runas-user'] = options.pop('runas')
            if options.get('conn') is not None:
                es_client = self.app.es_client_manager.get_client(int(options.pop('conn')))
            else:
                es_client = self.app.es_client
            if options:
                raise PeekError(f'Unknown options: {options}')
            self.app.display.info(es_client.perform_request(
                node.method, node.path, payload,
                headers=headers if headers else None))
        except Exception as e:
            if getattr(e, 'info', None) and isinstance(getattr(e, 'status_code', None), int):
                self.app.display.info(e.info)
            else:
                self.app.display.error(e)

    def visit_func_call_node(self, node: FuncCallNode):
        func_name = node.name_node.token.value
        func = self._get_value_for_name(func_name)
        if not callable(func):
            raise PeekError(f'{func_name!r} is not a callable, but a {func!r}')

        func_args = Ref()
        self.push_consumer(lambda v: func_args.set(v))
        node.args_node.accept(self)
        self.pop_consumer()

        for kv_node in node.kwargs_node.kv_nodes:
            assert isinstance(kv_node.key_node, NameNode), f'{kv_node.key_node!r}'
        func_kwargs = Ref()
        self.push_consumer(lambda v: func_kwargs.set(v))
        self._do_visit_dict_node(node.kwargs_node, resolve_key_name=False)
        self.pop_consumer()
        try:
            self.app.display.info(func(self.app, *func_args.get(), **func_kwargs.get()))
        except Exception as e:
            self.app.display.info(e)

    def visit_key_value_node(self, node: KeyValueNode):
        node.key_node.accept(self)
        node.value_node.accept(self)

    def visit_name_node(self, node: NameNode):
        v = self._get_value_for_name(node.token.value)
        self.consume(v)

    def visit_string_node(self, node: StringNode):
        self.consume(ast.literal_eval(node.token.value))

    def visit_number_node(self, node: NumberNode):
        self.consume(ast.literal_eval(node.token.value))

    def visit_dict_node(self, node: DictNode):
        self._do_visit_dict_node(node, resolve_key_name=True)

    def visit_array_node(self, node: ArrayNode):
        values = []
        self.push_consumer(lambda v: values.append(v))
        for node in node.value_nodes:
            node.accept(self)
        self.pop_consumer()
        self.consume(values)

    def visit_text_node(self, node: TextNode):
        self.consume(node.token.value)

    def _do_visit_dict_node(self, node: DictNode, resolve_key_name=False):
        assert isinstance(node, DictNode)
        keys = []
        values = []
        self.push_consumer(lambda v: keys.append(v))
        for kv_node in node.kv_nodes:
            if resolve_key_name or not isinstance(kv_node.key_node, NameNode):
                kv_node.key_node.accept(self)
            else:
                self.consume(kv_node.key_node.token.value)
            self.push_consumer(lambda v: values.append(v))
            kv_node.value_node.accept(self)
            self.pop_consumer()
        self.pop_consumer()
        assert len(keys) == len(values), f'{keys!r}, {values!r}'
        self.consume(dict(zip(keys, values)))

    def _get_value_for_name(self, name):
        value = self.builtin_names.get(name)
        if value is None:
            value = self.names.get(name)
        if value is None:
            raise PeekError(f'Unknown name: {name!r}')
        return value

    def _load_extensions(self):
        """
        Load extra variables from external paths
        """
        extension_path = self.app.config['extension_path']
        if not extension_path:
            return

        sys_path = sys.path[:]
        try:
            for p in extension_path.split(':'):
                if os.path.isfile(p):
                    self._load_one_extension(p)
                elif os.path.isdir(p):
                    for f in os.listdir(p):
                        if not f.endswith('.py'):
                            continue
                        self._load_one_extension(f)
        finally:
            sys.path = sys_path

    def _load_one_extension(self, p):
        import importlib
        fields = os.path.splitext(p)
        if len(fields) != 2 or fields[1] != '.py':
            _logger.warning(f'Extension must be python files, got: {p}')
            return
        sys.path.insert(0, os.path.dirname(fields[0]))
        try:
            m = importlib.import_module(os.path.basename(fields[0]))
            if isinstance(m.EXPORTS, dict):
                self.names.update(m.EXPORTS)
                _logger.info(f'Loaded extension: {p!r}')
            else:
                _logger.warning(f'Ignore extension {p!r} since EXPORTS is not a dict, but: {m.NAMES!r}')
        except Exception as e:
            _logger.warning(f'Error on loading extension: {p!r}, {e}')