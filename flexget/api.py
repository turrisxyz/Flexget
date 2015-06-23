import logging
import os
import base64
import hashlib
import random
import copy

from time import sleep
from functools import wraps
from collections import deque


from flask import Flask, request, jsonify, Response
from flask_restplus import Api as RestPlusAPI
from flask_restplus.resource import Resource
from flask_restplus.model import ApiModel
from jsonschema.exceptions import RefResolutionError
from werkzeug.exceptions import HTTPException

from flexget import __version__
from flexget.event import event
from flexget.webserver import register_app
from flexget.config_schema import process_config, register_config_key, schema_paths
from flexget import manager
from flexget.utils import json
from flexget.utils.database import with_session
from flexget.options import get_parser


API_VERSION = 1

log = logging.getLogger('api')


def generate_key():
    """ Generate api key for use to authentication """
    return base64.b64encode(hashlib.sha256(str(random.getrandbits(256))).digest(),
                            random.choice(['rA', 'aZ', 'gQ', 'hH', 'hG', 'aR', 'DD'])).rstrip('==')


api_config_schema = {
    'oneOf': [
        {'type': 'boolean'},
        {
            'type': 'object',
            'properties': {
                'api_key': {'type': 'string', 'default': generate_key()}
            },
            'additionalProperties': False
        }
    ],
    'additionalProperties': False
}


@event('config.register')
def register_config():
    register_config_key('api', api_config_schema)


class ApiSchemaModel(ApiModel):
    """A flask restplus :class:`flask_restplus.models.ApiModel` which can take a json schema directly."""
    def __init__(self, schema, *args, **kwargs):
        self._schema = schema
        super(ApiSchemaModel, self).__init__()

    @property
    def __schema__(self):
        if self.__parent__:
            return {
                'allOf': [
                    {'$ref': '#/definitions/{0}'.format(self.__parent__.name)},
                    self._schema
                ]
            }
        else:
            return self._schema

    def __nonzero__(self):
        return bool(self._schema)

    def __repr__(self):
        return '<ApiSchemaModel(%r)>' % self._schema


class Api(RestPlusAPI):
    """
    Extends a flask restplus :class:`flask_restplus.Api` with:
      - methods to make using json schemas easier
      - methods to auto document and handle :class:`ApiError` responses
    """

    def schema(self, name, schema, **kwargs):
        """
        Register a json schema.

        Usable like :meth:`flask_restplus.Api.model`, except takes a json schema as its argument.

        :returns: An :class:`ApiSchemaModel` instance registered to this api.
        """
        return self.model(name, **kwargs)(ApiSchemaModel(schema))

    def inherit(self, name, parent, fields):
        """
        Extends :meth:`flask_restplus.Api.inherit` to allow `fields` to be a json schema, if `parent` is a
        :class:`ApiSchemaModel`.
        """
        if isinstance(parent, ApiSchemaModel):
            model = ApiSchemaModel(fields)
            model.__apidoc__['name'] = name
            model.__parent__ = parent
            self.models[name] = model
            return model
        return super(Api, self).inherit(name, parent, fields)

    def validate(self, model):
        """
        When a method is decorated with this, json data submitted to the endpoint will be validated with the given
        `model`. This also auto-documents the expected model, as well as the possible :class:`ValidationError` response.
        """
        def decorator(func):
            @api.expect(model)
            @api.response(ValidationError)
            @wraps(func)
            def wrapper(*args, **kwargs):
                payload = request.json
                try:
                    errors = process_config(config=payload, schema=model.__schema__, set_defaults=False)
                    if errors:
                        raise ValidationError(errors)
                except RefResolutionError as e:
                    raise ApiError(str(e))
                return func(*args, **kwargs)
            return wrapper
        return decorator

    def response(self, code_or_apierror, description=None, model=None, **kwargs):
        """
        Extends :meth:`flask_restplus.Api.response` to allow passing an :class:`ApiError` class instead of
        response code. If an `ApiError` is used, the response code, and expected response model, is automatically
        documented.
        """
        try:
            if issubclass(code_or_apierror, ApiError):
                description = description or code_or_apierror.description
                return self.doc(responses={code_or_apierror.code: (description, code_or_apierror.response_model)})
        except TypeError:
            # If first argument isn't a class this happens
            pass
        return super(Api, self).response(code_or_apierror, description)

    def handle_error(self, error):
        """Responsible for returning the proper response for errors in api methods."""
        if isinstance(error, ApiError):
            return jsonify(error.to_dict()), error.code
        elif isinstance(error, HTTPException):
            return jsonify({'code': error.code, 'error': error.description}), error.code
        return super(Api, self).handle_error(error)


class APIResource(Resource):
    """All api resources should subclass this class."""
    method_decorators = [with_session]

    def __init__(self):
        self.manager = manager.manager
        super(APIResource, self).__init__()

app = Flask(__name__)
api = Api(app, catch_all_404s=True, title='Flexget API')


class ApiError(Exception):
    code = 500
    description = 'server error'

    response_model = api.schema('error', {
        'type': 'object',
        'properties': {
            'code': {'type': 'integer'},
            'error': {'type': 'string'}
        },
        'required': ['code', 'error']
    })

    def __init__(self, message, payload=None):
        self.message = message
        self.payload = payload

    def to_dict(self):
        rv = self.payload or {}
        rv.update(code=self.code, error=self.message)
        return rv

    @classmethod
    def schema(cls):
        return cls.response_model.__schema__


class NotFoundError(ApiError):
    code = 404
    description = 'not found'


class ValidationError(ApiError):
    code = 400
    description = 'validation error'

    response_model = api.inherit('validation_error', ApiError.response_model, {
        'type': 'object',
        'properties': {
            'validation_errors': {
                'type': 'array',
                'items': {
                    'type': 'object',
                    'properties': {
                        'message': {'type': 'string', 'description': 'A human readable message explaining the error.'},
                        'validator': {'type': 'string', 'description': 'The name of the failed validator.'},
                        'validator_value': {
                            'type': 'string', 'description': 'The value for the failed validator in the schema.'
                        },
                        'path': {'type': 'string'},
                        'schema_path': {'type': 'string'},
                    }
                }
            }
        },
        'required': ['validation_errors']
    })

    verror_attrs = (
        'message', 'cause', 'validator', 'validator_value',
        'path', 'schema_path', 'parent'
    )

    def __init__(self, validation_errors, message='validation error'):
        payload = {'validation_errors': [self._verror_to_dict(error) for error in validation_errors]}
        super(ValidationError, self).__init__(message, payload=payload)

    def _verror_to_dict(self, error):
        error_dict = {}
        for attr in self.verror_attrs:
            if isinstance(getattr(error, attr), deque):
                error_dict[attr] = list(getattr(error, attr))
            else:
                error_dict[attr] = getattr(error, attr)
        return error_dict


@event('manager.daemon.started')
def register_api(mgr):
    api_config = mgr.config.get('api')

    if api_config:
        register_app('/api', app)


# Schema API
schema_api = api.namespace('schema', description='Flexget JSON schema')

@schema_api.route('/')
class schemasAPI(APIResource):

    def get(self, session=None):
        return {'schemas': jsonify(schema_paths)}


@schema_api.route('/<path:path>')
@api.doc(params={'path': 'Path of schema'})
@api.response(404, 'invalid schema path')
class schemaAPI(APIResource):

    def get(self, path, session=None):
        path = '/schema/%s' % path
        if path in schema_paths:
            return schema_paths[path]
        return {'error': 'invalid schema path'}, 404


# Server API
server_api = api.namespace('server', description='Manage Flexget Daemon')


@server_api.route('/reload/')
class ServerReloadAPI(APIResource):

    @api.response(ApiError, 'Error loading the config')
    @api.response(200, 'Reloaded config')
    def get(self, session=None):
        """ Reload Flexget config """
        log.info('Reloading config from disk.')
        try:
            self.manager.load_config()
        except ValueError as e:
            raise ApiError('Error loading config: %s' % e.args[0])

        log.info('Config successfully reloaded from disk.')
        return {}

pid_schema = api.schema('server_pid', {
    'type': 'object',
    'properties': {
        'pid': {
            'type': 'integer'
        }
    }
})


@server_api.route('/pid/')
class ServerPIDAPI(APIResource):
    @api.response(200, 'Reloaded config', pid_schema)
    def get(self, session=None):
        """ Get server PID """
        return{'pid': os.getpid()}


shutdown_parser = api.parser()
shutdown_parser.add_argument('force', type=bool, required=False, default=False, help='Ignore tasks in the queue')


@server_api.route('/shutdown/')
class ServerShutdownAPI(APIResource):
    @api.doc(parser=shutdown_parser)
    @api.response(200, 'Shutdown requested')
    def get(self, session=None):
        """ Shutdown Flexget Daemon """
        args = shutdown_parser.parse_args()
        self.manager.shutdown(args['force'])
        return {}


@server_api.route('/config/')
class ServerConfigAPI(APIResource):
    @api.response(200, 'Flexget config')
    def get(self, session=None):
        """ Get Flexget Config """
        return self.manager.config


version_schema = api.schema('version', {
    'type': 'object',
    'properties': {
        'flexget_version': {'type': 'string'},
        'api_version': {'type': 'integer'}
    }
})


@server_api.route('/version/')
class ServerVersionAPI(APIResource):
    @api.response(200, 'Flexget version', version_schema)
    def get(self, session=None):
        """ Flexget Version """
        return {'flexget_version': __version__, 'api_version': API_VERSION}


@server_api.route('/log/')
class ServerLogAPI(APIResource):
    @api.response(200, 'Streams as line delimited JSON')
    def get(self, session=None):
        """ Stream Flexget log
        Streams as line delimited JSON
        """
        def tail():
            f = open(os.path.join(self.manager.config_base, 'log-%s.json' % self.manager.config_name), 'r')
            while True:
                line = f.readline()
                if not line:
                    sleep(0.1)
                    continue
                yield line

        return Response(tail(), mimetype='text/event-stream')


# Execution API
execution_api = api.namespace('execution', description='Execute tasks')


def _task_info_dict(task_info):
    return {
        'id': int(task_info.id),
        'name': task_info.name,
        'status': task_info.status,
        'created': task_info.created,
        'started': task_info.started,
        'finished': task_info.finished,
        'message': task_info.message,
        'log': {'href': '/execution/%s/log/' % task_info.id},
    }


task_execution_api_schema = {
    "type": "object",
    "properties": {
        "created": {"type": "string"},
        "finished": {"type": "string"},
        "id": {"type": "integer"},
        "log": {
            "type": "object",
            "properties": {
                "href": {
                    "type": "string"
                }
            }
        },
        "message": {"type": "string"},
        "name": {"type": "string"},
        "started": {"type": "string"},
        "status": {"type": "string"}
    }
}

tasks_execution_api_schema = {
    "type": "object",
    "properties": {
        "tasks": {
            "type": "array",
            "items": task_execution_api_schema
        }
    }
}


task_execution_api_schema = api.schema('task_execution', task_execution_api_schema)
tasks_execution_api_schema = api.schema('tasks_execution', tasks_execution_api_schema)


@execution_api.route('/')
@api.doc(description='Execution ID are held in memory, they will be lost upon daemon restart')
class ExecutionAPI(APIResource):

    @api.response(200, 'list of task executions', tasks_execution_api_schema)
    def get(self, session=None):
        """ List task executions
        List current, pending and previous(hr max) executions
        """
        tasks = [_task_info_dict(task_info) for task_info in self.manager.task_queue.tasks_info.itervalues()]
        return jsonify({"tasks": tasks})

    @api.validate(tasks_execution_api_schema)
    @api.response(400, 'invalid options specified')
    @api.response(200, 'list of tasks queued for execution')
    def post(self, session=None):
        """ Execute task
        Return a unique execution ID for tracking and log streaming
        """
        kwargs = request.json or {}

        options_string = kwargs.pop('options_string', '')
        if options_string:
            try:
                kwargs['options'] = get_parser('execute').parse_args(options_string, raise_errors=True)
            except ValueError as e:
                return {'error': 'invalid options_string specified: %s' % e.message}, 400

        tasks = self.manager.execute(**kwargs)

        return {"tasks": [_task_info_dict(self.manager.task_queue.tasks_info[task_id]) for task_id, event in tasks]}


@api.doc(params={'exec_id': 'Execution ID of the Task'})
@api.doc(description='Execution ID are held in memory, they will be lost upon daemon restart')
@execution_api.route('/<exec_id>/')
class ExecutionTaskAPI(APIResource):

    @api.response(NotFoundError, 'task execution not found')
    @api.response(200, 'list of tasks queued for execution', task_execution_api_schema)
    def get(self, exec_id, session=None):
        """ Status of existing task execution """
        task_info = self.manager.task_queue.tasks_info.get(exec_id)

        if not task_info:
            raise NotFoundError('%s not found' % exec_id)

        return _task_info_dict(task_info)


@api.doc(params={'exec_id': 'Execution ID of the Task'})
@api.doc(description='Execution ID are held in memory, they will be lost upon daemon restart')
@execution_api.route('/<exec_id>/log/')
class ExecutionTaskLogAPI(APIResource):
    @api.response(200, 'Streams as line delimited JSON')
    @api.response(NotFoundError, 'task log not found')
    def get(self, exec_id, session=None):
        """ Log stream of executed task
        Streams as line delimited JSON
        """
        task_info = self.manager.task_queue.tasks_info.get(exec_id)

        if not task_info:
            raise NotFoundError('%s not found' % exec_id)

        def follow():
            f = open(os.path.join(self.manager.config_base, 'log-%s.json' % self.manager.config_name), 'r')
            while True:
                if not task_info.started:
                    continue

                # First check if it has finished, if there is no new lines then we can return
                finished = task_info.finished is not None
                line = f.readline()
                if not line:
                    if finished:
                        return
                    sleep(0.1)
                    continue

                record = json.loads(line)
                if record['task_id'] != exec_id:
                    continue
                yield line

        return Response(follow(), mimetype='text/event-stream')


# Tasks API
tasks_api = api.namespace('tasks', description='Manage Tasks')

task_api_schema = {
    'type': 'object',
    'properties': {
        'name': {'type': 'string'},
        'config': {'$ref': '/schema/plugins'}
    },
    'additionalProperties': False
}

tasks_api_schema = {
    "type": "object",
    "properties": {
        "tasks": {
            "type": "array",
            "items": task_api_schema
        }
    },
    'additionalProperties': False
}

tasks_api_schema = api.schema('tasks', tasks_api_schema)
task_api_schema = api.schema('task', task_api_schema)


@tasks_api.route('/')
class TasksAPI(APIResource):

    @api.response(200, 'list of tasks', tasks_api_schema)
    def get(self, session=None):
        """ Show all tasks """

        tasks = []
        for name, config in self.manager.user_config.get('tasks', {}).iteritems():
            tasks.append({'name': name, 'config': config})
        return {'tasks': tasks}

    @api.validate(task_api_schema)
    @api.response(201, 'newly created task', task_api_schema)
    @api.response(409, 'task already exists', task_api_schema)
    def post(self, session=None):
        """ Add new task """
        data = request.json

        task_name = data['name']

        if task_name in self.manager.user_config.get('tasks', {}):
            return {'error': 'task already exists'}, 409

        if 'tasks' not in self.manager.user_config:
            self.manager.user_config['tasks'] = {}
        if 'tasks' not in self.manager.config:
            self.manager.config['tasks'] = {}

        task_schema_processed = copy.deepcopy(data)
        errors = process_config(task_schema_processed, schema=task_api_schema.__schema__, set_defaults=True)

        if errors:
            return {'error': 'problem loading config, raise a BUG as this should not happen!'}, 500

        self.manager.user_config['tasks'][task_name] = data['config']
        self.manager.config['tasks'][task_name] = task_schema_processed['config']

        self.manager.save_config()
        self.manager.config_changed()
        return {'name': task_name, 'config': self.manager.user_config['tasks'][task_name]}, 201


@tasks_api.route('/<task>/')
@api.doc(params={'task': 'task name'})
class TaskAPI(APIResource):

    @api.response(200, 'task config', task_api_schema)
    @api.response(NotFoundError, 'task not found')
    @api.response(ApiError, 'unable to read config')
    def get(self, task, session=None):
        """ Get task config """
        if task not in self.manager.user_config.get('tasks', {}):
            raise NotFoundError('task `%s` not found' % task)

        return {'name': task, 'config': self.manager.user_config['tasks'][task]}

    @api.validate(task_api_schema)
    @api.response(200, 'updated task', task_api_schema)
    @api.response(201, 'renamed task', task_api_schema)
    @api.response(404, 'task does not exist', task_api_schema)
    @api.response(400, 'cannot rename task as it already exist', task_api_schema)
    def post(self, task, session=None):
        """ Update tasks config """
        data = request.json

        new_task_name = data['name']

        if task not in self.manager.user_config.get('tasks', {}):
            return {'error': 'task does not exist'}, 404

        if 'tasks' not in self.manager.user_config:
            self.manager.user_config['tasks'] = {}
        if 'tasks' not in self.manager.config:
            self.manager.config['tasks'] = {}

        code = 200
        if task != new_task_name:
            # Rename task
            if new_task_name in self.manager.user_config['tasks']:
                return {'error': 'cannot rename task as it already exist'}, 400

            del self.manager.user_config['tasks'][task]
            del self.manager.config['tasks'][task]
            code = 201

        # Process the task config
        task_schema_processed = copy.deepcopy(data)
        errors = process_config(task_schema_processed, schema=task_api_schema.__schema__, set_defaults=True)

        if errors:
            return {'error': 'problem loading config, raise a BUG as this should not happen!'}, 500

        self.manager.user_config['tasks'][new_task_name] = data['config']
        self.manager.config['tasks'][new_task_name] = task_schema_processed['config']

        self.manager.save_config()
        self.manager.config_changed()

        return {'name': new_task_name, 'config': self.manager.user_config['tasks'][new_task_name]}, code

    @api.response(200, 'deleted task')
    @api.response(404, 'task not found')
    def delete(self, task, session=None):
        """ Delete a task """
        try:
            self.manager.config['tasks'].pop(task)
            self.manager.user_config['tasks'].pop(task)
        except KeyError:
            return {'error': 'invalid task'}, 404

        self.manager.save_config()
        self.manager.config_changed()
        return {}


class ApiClient(object):
    """
    This is an client which can be used as a more pythonic interface to the rest api.

    It skips http, and is only usable from within the running flexget process.
    """
    def __init__(self):
        app = Flask(__name__)
        app.register_blueprint(api)
        self.app = app.test_client()

    def __getattr__(self, item):
        return ApiEndopint('/api/' + item, self.get_endpoint)

    def get_endpoint(self, url, data, method=None):
        if method is None:
            method = 'POST' if data is not None else 'GET'
        response = self.app.open(url, data=data, follow_redirects=True, method=method)
        result = json.loads(response.data)
        # TODO: Proper exceptions
        if 200 > response.status_code >= 300:
            raise Exception(result['error'])
        return result


class ApiEndopint(object):
    def __init__(self, endpoint, caller):
        self.endpoint = endpoint
        self.caller = caller

    def __getattr__(self, item):
        return self.__class__(self.endpoint + '/' + item, self.caller)

    __getitem__ = __getattr__

    def __call__(self, data=None, method=None):
        return self.caller(self.endpoint, data=data, method=method)
