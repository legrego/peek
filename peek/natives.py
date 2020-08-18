import logging

from configobj import ConfigObj

from peek.connection import ConnectFunc
from peek.errors import PeekError
from peek.krb import KrbAuthenticateFunc
from peek.oidc import OidcAuthenticateFunc
from peek.saml import SamlAuthenticateFunc

_logger = logging.getLogger(__name__)


class ConfigFunc:

    def __call__(self, app, **options):
        if not options:
            return app.config

        extra_config = {}
        for key, value in options.items():
            parent = extra_config
            key_components = key.split('.')
            for key_component in key_components[:-1]:
                child = parent.get(key_component)
                if child is None:
                    parent[key_component] = {}
                elif not isinstance(child, dict):
                    _logger.warning(f'Config key [{key}] conflicts. '
                                    f'Value of [{key_component}] is not a [dict], '
                                    f'but [{type(child)}]')
                    parent = None
                    break
                parent = parent[key_component]

            if isinstance(parent, dict):
                parent[key_components[-1]] = value

        # TODO: saner merge that does not change data type, e.g. from dict to primitive and vice versa
        app.config.merge(ConfigObj(extra_config))


class SessionFunc:

    def __call__(self, app, current=None, **options):
        current = current if current is not None else options.get('current', None)
        if isinstance(current, str):
            app.es_client_manager.set_current_by_name(current)
        elif current is not None:
            app.es_client_manager.set_current(int(current))

        remove = options.get('remove', None)
        if isinstance(remove, str):
            app.es_client_manager.remove_client_by_name(remove)
        elif remove is not None:
            app.es_client_manager.remove_client(int(remove))

        rename = options.get('rename', None)
        if rename:
            app.es_client_manager.current.name = str(rename)

        info = options.get('info', None)
        if isinstance(info, str):
            return app.es_client_manager.get_client_by_name(info).info()
        elif info is not None:
            return app.es_client_manager.get_client(int(info)).info()

        return str(app.es_client_manager)

    @property
    def options(self):
        return {'current': None, 'remove': None, 'rename': None, 'info': None}


class RunFunc:

    def __call__(self, app, file, **options):
        with open(file) as ins:
            app.process_input(ins.read(), echo=options.get('echo', False))

    @property
    def options(self):
        return {'echo': False}


class HistoryFunc:

    def __call__(self, app, index=None, **options):
        if index is None:
            history = []
            for entry in app.history.load_recent():
                history.append(f'{entry[0]:>6} {entry[1]!r}')
            return '\n'.join(history)
        else:
            entry = app.history.get_entry(index)
            if entry is None:
                raise PeekError(f'History not found for index: {index}')
            app.process_input(entry[1])


class HelpFunc:

    def __call__(self, app, func=None, **options):
        if func is None:
            return '\n'.join(app.vm.functions.keys())

        for k, v in app.vm.functions.items():
            if v == func:
                return f'{k}\n{getattr(func, "options", {})}'
        else:
            raise PeekError(f'No such function: {func}')


EXPORTS = {
    'connect': ConnectFunc(),
    'config': ConfigFunc(),
    'session': SessionFunc(),
    'run': RunFunc(),
    'history': HistoryFunc(),
    'help': HelpFunc(),
    'saml_authenticate': SamlAuthenticateFunc(),
    'oidc_authenticate': OidcAuthenticateFunc(),
    'krb_authenticate': KrbAuthenticateFunc(),
}
