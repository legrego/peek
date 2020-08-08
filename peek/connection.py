import json
import logging
import os
from abc import ABCMeta, abstractmethod
from enum import Enum

from elasticsearch import Elasticsearch, AuthenticationException
from urllib3 import Timeout

from peek.errors import PeekError

_logger = logging.getLogger(__name__)


class NoopDeserializer:
    def __init__(self):
        pass

    def loads(self, s, *args, **kwargs):
        return s


noopDeserializer = NoopDeserializer()


class BaseClient(metaclass=ABCMeta):

    @abstractmethod
    def perform_request(self, method, path, payload, deserialize_it=False, **kwargs):
        pass


class EsClient(BaseClient):

    def __init__(self,
                 hosts='localhost:9200',
                 username=None,
                 password=None,
                 use_ssl=False,
                 verify_certs=False,
                 ca_certs=None,
                 client_cert=None,
                 client_key=None,
                 **kwargs):

        self.hosts = ['localhost:9200'] if hosts is None else hosts.split(',')
        self.auth = f'{username}:{password}' if username and password else None
        self.use_ssl = use_ssl
        self.verify_certs = verify_certs
        self.ca_certs = ca_certs
        self.client_cert = client_cert
        self.client_key = client_key

        self.es = Elasticsearch(
            hosts=self.hosts,
            http_auth=self.auth,
            use_ssl=use_ssl,
            verify_certs=verify_certs,
            ca_certs=ca_certs,
            client_cert=client_cert,
            client_key=client_key,
            ssl_show_warn=False,
            timeout=Timeout(connect=None, read=None),
            **kwargs,
        )

    def perform_request(self, method, path, payload, deserialize_it=False, **kwargs):
        _logger.debug(f'Performing request: {method!r}, {path!r}, {payload!r}')
        deserializer = self.es.transport.deserializer
        try:
            if not deserialize_it:
                # Avoid deserializing the response since we parse it with the main loop for syntax highlighting
                self.es.transport.deserializer = noopDeserializer
            return self.es.transport.perform_request(method, path, body=payload, **kwargs)
        finally:
            if not deserialize_it:
                self.es.transport.deserializer = deserializer

    def __str__(self):
        hosts = []
        for host in self.hosts:
            if host.startswith('https://') or host.startswith('http://'):
                hosts.append(host)
            else:
                hosts.append(('https://' if self.use_ssl else 'http://') + host)

        hosts = ','.join(hosts)
        username = '' if self.auth is None else self.auth.split(':')[0]
        return f'{username} @ {hosts}'


class RefreshingEsClient(BaseClient):
    # TODO: Given a pair of access_token and refresh token, refresh to keep login

    def __init__(self,
                 parent: EsClient,
                 username,
                 access_token,
                 refresh_token,
                 expires_in):

        self.parent = parent
        self.username = username
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.expires_in = expires_in
        self.delegate = self._build_delegate()

    def perform_request(self, method, path, payload, deserialize_it=False, **kwargs):
        try:
            return self.delegate.perform_request(method, path, payload, deserialize_it, **kwargs)
        except AuthenticationException as e:
            if e.status_code == 401:
                response = self.parent.perform_request(
                    'POST', '/_security/oauth2/token',
                    json.dumps({
                        'grant_type': 'refresh_token',
                        'refresh_token': self.refresh_token,
                    }),
                    deserialize_it=True)
                self.access_token = response['access_token']
                self.refresh_token = response['refresh_token']
                self.expires_in = response['expires_in']
                self._build_delegate()
                self.perform_request(method, path, payload, deserialize_it=deserialize_it, **kwargs)

    def __str__(self):
        return self.username + str(self.delegate)

    def _build_delegate(self):
        return EsClient(
            ','.join(self.parent.hosts),
            use_ssl=self.parent.use_ssl,
            verify_certs=self.parent.verify_certs,
            ca_certs=self.parent.ca_certs,
            client_cert=self.parent.client_cert,
            client_key=self.parent.client_key,
            headers={'Authorization': f'Bearer {self.access_token}'},
        )


class EsClientManger:

    def __init__(self):
        self._clients = []
        self._index_current = None

    def add(self, client):
        self._clients.append(client)
        self._index_current = len(self._clients) - 1
        # TODO: maintain size

    @property
    def current(self):
        if self._index_current is None:
            raise PeekError('No ES client is configured')
        if self._index_current < 0 or self._index_current >= len(self._clients):
            raise PeekError(f'Attempt to get ES client at invalid index [{self._index_current}]')
        return self._clients[self._index_current]

    @current.setter
    def current(self, i):
        if i < 0 or i >= len(self._clients):
            raise PeekError(f'Attempt to set ES client at invalid index [{i}]')
        self._index_current = i

    @property
    def index_current(self):
        return self._index_current

    def clients(self):
        return self._clients

    def get_client(self, i):
        if i < 0 or i >= len(self._clients):
            raise PeekError(f'Attempt to set ES client at invalid index [{i}]')
        return self._clients[i]

    def remove_client(self, i):
        if len(self._clients) == 1:
            raise PeekError('Cannot delete the last connection')
        if i < 0 or i >= len(self._clients):
            raise PeekError(f'Attempt to remove ES client at invalid index [{i}]')
        self._clients.pop(i)
        if not self._clients:
            self._index_current = None
            return
        if i < self._index_current:
            self._index_current -= 1
        elif i == self._index_current:
            self._index_current = 0

    def __str__(self):
        lines = []
        for i, client in enumerate(self.clients()):
            prefix = '*' if client == self.current else ' '
            index = f'[{i}]'
            lines.append(f'{prefix} {index:>4} {client}')
        return '\n'.join(lines)


class AuthType(Enum):
    USERPASS = 'USERPASS'
    APIKEY = 'APIKEY'
    TOKEN = 'TOKEN'
    SAML = 'SAML'
    OIDC = 'OIDC'
    KRB = 'KRB'
    PKI = 'PKI'


def connect(app, **options):
    final_options = {
        'hosts': 'localhost:9200',
        'auth_type': AuthType.USERPASS,
        'username': None,
        'password': None,
        'api_key': None,
        'use_ssl': None,
        'verify_certs': False,
        'ca_certs': None,
        'client_cert': None,
        'client_key': None,
        'force_prompt': False,
        'no_prompt': False,
    }

    if 'auth_type' in options:
        options['auth_type'] = AuthType(options['auth_type'])
    final_options.update({k: v for k, v in options.items() if v is not None})

    if final_options['auth_type'] is AuthType.APIKEY or final_options['api_key']:
        return _connect_api_key(app, **final_options)
    elif final_options['auth_type'] is AuthType.USERPASS:
        return _connect_userpass(app, **final_options)
    else:
        raise NotImplementedError(f'{final_options["auth_type"]}')


def _connect_userpass(app, **options):
    username = options.get('username')
    password = options.get('password')
    service_name = f'peek/{options["hosts"]}/userpass'

    if not username and password:
        raise PeekError(f'Username is required for userpass authentication')

    if options['force_prompt']:
        password = app.input(message='Please enter password: ', is_secret=True)

    if username and not password:
        password = os.environ.get('PEEK_PASSWORD', None)
        if not password:
            if app.config.as_bool('use_keyring'):
                password = _keyring(service_name, username)
                if not password:
                    if options['no_prompt']:
                        raise PeekError('Password is not found and password prompt is disabled')
                    password = app.input(message='Please enter password: ', is_secret=True)

            else:
                if options['no_prompt']:
                    raise PeekError('Password is not found and password prompt is disabled')
                password = app.input(message='Please enter password: ', is_secret=True)

    if username and password and app.config.as_bool('use_keyring'):
        _keyring(service_name, username, password)

    return EsClient(
        hosts=options['hosts'],
        username=username,
        password=password,
        use_ssl=options['use_ssl'],
        verify_certs=options['verify_certs'],
        ca_certs=options['ca_certs'],
        client_cert=options['client_cert'],
        client_key=options['client_key'])


def _connect_api_key(app, **options):
    _logger.debug(f'Connecting with API key')
    return EsClient(
        hosts=options['hosts'],
        api_key=options['api_key'].split(':'),
        use_ssl=options['use_ssl'],
        verify_certs=options['verify_certs'],
        ca_certs=options['ca_certs'],
        client_cert=options['client_cert'],
        client_key=options['client_key'],
    )


KEYRING = None


def _keyring(service_name, key, value=None):
    """
    When a None value is passed in, means getting value out of the keyring
    """
    global KEYRING
    if KEYRING is None:
        import importlib
        try:
            KEYRING = importlib.import_module("keyring")
        except ModuleNotFoundError as e:
            _logger.warning("import keyring failed: %r.", e)

    if KEYRING is not None:
        if value is None:
            return KEYRING.get_password(service_name, key)
        else:
            KEYRING.set_password(service_name, key, value)