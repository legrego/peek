import json
import logging

from configobj import ConfigObj

from peek.connection import connect
from peek.saml import saml_authenticate

_logger = logging.getLogger(__name__)


def func_config(app, **options):
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


def func_connect(app, **options):
    app.es_client_manager.add(connect(app, **options))
    return str(app.es_client_manager)


def func_connections(app, current=None, **options):
    if current is not None:
        app.es_client_manager.current = int(current)
    elif 'current' in options:
        app.es_client_manager.current = int(options['current'])

    if 'remove' in options:
        app.es_client_manager.remove_client(int(options['remove']))

    return str(app.es_client_manager)


def func_saml_authenticate(app, **options):
    realm = options.get('realm', 'saml1')
    saml_es_client = saml_authenticate(
        app.es_client,
        realm,
        options.get('callback_port', '5601'),
    )
    app.es_client_manager.add(saml_es_client)
    return json.dumps({'username': saml_es_client.username, 'realm': 'realm'})


NAMES = {
    'connect': func_connect,
    'config': func_config,
    'connections': func_connections,
    'saml_authenticate': func_saml_authenticate,
}