import json
import os
import re
import socket
import sys
from contextlib import contextmanager
from copy import deepcopy, copy
from os.path import join, dirname, abspath

from invoke import task
from pynginxconfig import NginxConfig


@contextmanager
def add_module_to_pythonpath():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    yield
    sys.path.pop(0)

with add_module_to_pythonpath():
    from utils import load_config, get_init_db_envs, create_init_db_file, get_extra_envs, normalize

BASE_DIR = dirname(abspath(__file__))

ADMIN_TEMPLATE = re.compile(r'^[a-zA-Z_]*_admin$')

SPECIAL_PARAM_TEMPLATE = re.compile(r'^{{(?P<get_command>[a-zA-Z_]*)}}$')

COMPOSE_TEMPLATE = {
    'version': '2',
    'services': {},
    'networks': {
        'default': {
            'external': {
                'name': 'services'
            }
        }
    }
}

PROJECT_TEMPLATE = {
    'restart': 'always',
    'build': {
        'context': '.',
        'args': {}
    },
    'volumes': [],
    'environment': {}
}

DOCKERFILE_TEMPLATE = '''FROM python:3.5-alpine

MAINTAINER PavelEgorov

RUN apk add --update alpine-sdk
RUN apk add cmake
RUN apk add linux-headers
RUN apk add libxslt-dev
RUN apk add libxml2-dev
RUN apk add libc-dev
RUN apk add postgresql-dev

RUN ln -s /usr/include/locale.h /usr/include/xlocale.h

ARG PROJECT_NAME
ARG APPLICATION_PORT

EXPOSE $APPLICATION_PORT

ENV PROJECT_NAME $PROJECT_NAME

COPY src/$PROJECT_NAME ./main_project

RUN pip install -r ./main_project/requirements.txt

WORKDIR /main_project

'''


def prepare_for_postgres(config, ctx, compose_conf_template):
    ctx.run('mkdir -p postgresql/data')
    compose_conf = deepcopy(compose_conf_template)

    init_db_envs = get_init_db_envs(config)
    for db_envs in init_db_envs.values():
        compose_conf['environment'].update(db_envs)

    create_init_db_file(init_db_envs)

    return compose_conf


def prepare_for_redis(_, ctx, compose_conf_template):
    ctx.run('mkdir -p redis/data')
    return compose_conf_template


def prepare_for_nginx(config, _, compose_conf_template):
    compose_conf = deepcopy(compose_conf_template)
    compose_conf['ports'] = [
        '{0}:{0}'.format(params['APPLICATION_PORT']) for params in config.values() if params.get('APPLICATION_PORT')
    ]

    nc = NginxConfig()
    nc.append(('user', 'nginx'))
    nc.append(('pid', '/var/run/nginx.pid'))
    nc.append(('worker_processes', '1'))
    nc.append(('error_log', 'stderr info'))
    nc.append({'name': 'events', 'param': '', 'value': [('worker_connections', '1024')]})

    http_conf = {
        'name': 'http',
        'param': '',
        'value': [
            ('include', '/etc/nginx/mime.types'),
            ('default_type', 'application/octet-stream'),
            ('log_format main', '$remote_addr [$time_local] "$request" $status $bytes_sent "$http_referer"'),
            ('sendfile', 'on'),
            ('keepalive_timeout', '65'),
            ('include', '/etc/nginx/conf.d/*.conf'),
        ]
    }

    for service_name, params in config.items():
        if 'nginx' in params['DEPENDS_ON']:
            project_name = params['PROJECT_NAME']

            server_conf = {
                'name': 'server',
                'param': '',
                'value': [
                    ('server_name', params['SERVER_NAME']),
                    ('charset', 'utf-8'),
                    ('client_max_body_size', '75M'),
                    {
                        'name': 'location',
                        'param': '/',
                        'value': [
                            ('uwsgi_pass', 'unix:///opt/sockets/{}.sock'.format(service_name)),
                            ('include', '/opt/uwsgi_params')
                        ]
                    },
                ]
            }

            if params['USE_SSL']:
                server_conf['value'].extend([
                    ('listen', '{} ssl'.format(params['APPLICATION_PORT'])),
                    ('ssl_certificate', '/opt/certs/{}'.format(params['SSL_CERT'])),
                    ('ssl_certificate_key', '/opt/certs/{}'.format(params['SSL_KEY'])),
                ])
            else:
                server_conf['value'].append(('listen', '{}'.format(params['APPLICATION_PORT'])))

            if params['USE_STATIC']:
                server_conf['value'].append({
                    'name': 'location',
                    'param': '/static',
                    'value': [('alias', '/opt/static/{}'.format(project_name))]
                })
            if params['USE_MEDIA']:
                server_conf['value'].append({
                    'name': 'location',
                    'param': '/media',
                    'value': [('alias', '/opt/media/{}'.format(project_name))]
                })
            http_conf['value'].append(server_conf)

    nc.append(http_conf)

    with open('nginx.conf', 'w') as f:
        f.write(nc.gen_config())

    return compose_conf

ADDITIONAL_SERVICES = {
    'postgres_db': {
        'compose_conf': {
            'restart': 'always',
            'build': {
                'context': '.',
                'dockerfile': 'PostgresInitDockerfile'
            },
            'volumes': ['{}:/var/lib/postgresql/data:z'.format(join(BASE_DIR, 'postgresql/data'))],
            'environment': {}
        },
        'init_command': prepare_for_postgres
    },
    'redis': {
        'compose_conf': {
            'restart': 'always',
            'image': 'redis:3-alpine',
            'volumes': ['{}:/data:z'.format(join(BASE_DIR, 'redis/data'))],
        },
        'init_command': prepare_for_redis
    },
    'nginx': {
        'compose_conf': {
            'restart': 'always',
            'image': 'nginx:1-alpine',
            'volumes': [
                '{}:/etc/nginx/nginx.conf:ro'.format(join(BASE_DIR, 'nginx.conf')),
                '{}:/opt/static/:ro'.format(join(BASE_DIR, 'static')),
                '{}:/opt/uwsgi_params:ro'.format(join(BASE_DIR, 'uwsgi_params')),
                '{}:/opt/certs:ro'.format(join(BASE_DIR, 'certs')),
                '{}:/opt/sockets/:z'.format(join(BASE_DIR, 'sockets')),
                '{}:/opt/media/:z'.format(join(BASE_DIR, 'media'))
            ]
        },
        'init_command': prepare_for_nginx
    },
}


@task
def install_docker(ctx):
    ctx.run('sudo yum install -y docker docker-registry')
    ctx.run('sudo groupadd docker')
    ctx.run('sudo usermod -aG docker {}'.format(os.environ['USER']))
    ctx.run('sudo systemctl enable docker.service')
    ctx.run('sudo systemctl start docker.service')


@task
def create_docker_network(ctx):
    ctx.run('docker network create services')


@task
@load_config('deployment.ini')
def prepare_files(config, ctx):
    compose_body = deepcopy(COMPOSE_TEMPLATE)
    dependencies = set()

    ctx.run('mkdir -p sockets')

    for service_name, params in config.items():
        dependencies.update(params.get('DEPENDS_ON', []).split(','))
        compose_body['services'][service_name] = _init_project(
            ctx,
            service_name,
            params['PROJECT_NAME'],
            normalize(params, {False: '', True: 'true'}),
            ADMIN_TEMPLATE.match(service_name)
        )

    for dependency in dependencies:
        if dependency in ADDITIONAL_SERVICES:
            init_command = ADDITIONAL_SERVICES[dependency]['init_command']
            compose_service_conf = init_command(config, ctx, ADDITIONAL_SERVICES[dependency]['compose_conf'])
            compose_body['services'][dependency] = compose_service_conf

    with open('docker-compose.json', 'w') as f:
        f.write(json.dumps(compose_body, indent=4, sort_keys=True))


@task
def build_services(ctx):
    ctx.run('/opt/python/bin/docker-compose -f docker-compose.json build')


@task
def up_services(ctx):
    ctx.run('/opt/python/bin/docker-compose -f docker-compose.json up -d')


@task
def chmod_sockets(ctx):
    ctx.run('sudo chmod 777 -R ./sockets')


@task
def ps_services(ctx):
    ctx.run('/opt/python/bin/docker-compose -f docker-compose.json ps')


@task
def down_services(ctx):
    ctx.run('/opt/python/bin/docker-compose -f docker-compose.json down', warn=True)


@task
def remove_images(ctx):
    ctx.run('docker rm -f `docker ps -a -q`', warn=True)
    ctx.run('docker rmi -f `docker images -a -q`', warn=True)


def _init_project(ctx, service_name, project_name, params, is_admin):
    project_settings = deepcopy(PROJECT_TEMPLATE)

    project_envs = copy(params)
    project_envs.update(get_extra_envs())

    for k, v in project_envs.items():
        match_result = SPECIAL_PARAM_TEMPLATE.match(v)
        if match_result:
            v = globals().get(match_result.group('get_command'), lambda *_: '')(params)
        project_settings['environment'][k] = v

    project_settings['environment']['IP_ADDRESS'] = params['PUBLIC_ADDRESS']

    if params['USE_STATIC']:
        ctx.run('mkdir -p static/{}'.format(project_name))
        server_static_dir = join(BASE_DIR, join('static', project_name))
        project_settings['volumes'].append('{}:/main_project/static:z'.format(server_static_dir))

    if params['USE_MEDIA']:
        ctx.run('mkdir -p media/{}'.format(project_name))
        server_media_dir = join(BASE_DIR, join('media', project_name))
        project_settings['volumes'].append('{}:/main_project/media:z'.format(server_media_dir))

    if 'nginx' in params['DEPENDS_ON']:
        project_settings['volumes'].append('{}:/main_project/sockets:z'.format(join(BASE_DIR, 'sockets')))

    project_settings['build']['args']['PROJECT_NAME'] = project_name
    project_settings['build']['args']['APPLICATION_PORT'] = params['APPLICATION_PORT']

    entry_point = 'ENTRYPOINT ' + params.get(
        'ENTRY_POINT',
        _get_admin_entrypoint(service_name, params) if is_admin else _get_entrypoint(service_name, params)
    )
    dockerfile_name = '{}_dockerfile'.format(service_name)
    with open(dockerfile_name, 'w') as f:
        f.write(DOCKERFILE_TEMPLATE)
        f.write(entry_point)

    project_settings['build']['dockerfile'] = dockerfile_name

    project_settings['depends_on'] = params.get('DEPENDS_ON', '').split(',')

    return project_settings


def _get_admin_entrypoint(service_name, params):
    result = 'sh -c "{}"'
    commands = [
        'python manage.py collectstatic --clear --noinput && ',
        'echo \\"from django.contrib.auth.models import User;',
        'create_superuser = User.objects.create_superuser;',
        "is_exists = User.objects.filter(username='{}').exists();".format(params['ADMIN_USER_NAME']),
        "[create_superuser('{}', '{}', '{}') for i in range(1) if not is_exists]\\\" | ".format(
            params['ADMIN_USER_NAME'], params['ADMIN_EMAIL'], params['ADMIN_PASSWORD']
        ),
        'python manage.py shell && ',
        'uwsgi --socket /main_project/sockets/{}.sock --module {}.wsgi:application --master'.format(
            service_name, params['PROJECT_NAME']
        )
    ]
    return result.format(''.join(commands))


def _get_entrypoint(service_name, params):
    result = 'sh -c "{}"'
    commands = []
    if params['USE_MIGRATIONS']:
        commands.append('python manage.py migrate --fake-initial --noinput && ')
    if params['USE_STATIC']:
        commands.append('python manage.py collectstatic --clear --noinput && ')
    commands.append('uwsgi --socket /main_project/sockets/{}.sock --module {}.wsgi:application --master'.format(
        service_name, params['PROJECT_NAME']
    ))
    return result.format(''.join(commands))


def get_local_ip(*_):
    return socket.gethostbyname(socket.gethostname())
