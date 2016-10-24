import os
import re
from contextlib import contextmanager
from shutil import rmtree

from fabric.api import env, cd, put, run as _run, sudo as _sudo  # noqa
from fabric.network import connect, normalize, HostConnectionCache, join_host_strings  # noqa
from fabric.state import connections  # noqa
from wrapt import decorator

from deployment_tools.utils import load_config

ATTEMPTS = 10


@decorator
def _ignore_timeout_wrapper(wrapped, _, args, kwargs):
    try:
        return wrapped(*args, **kwargs)
    except TimeoutError:
        return wrapped(*args, **kwargs)

run = _ignore_timeout_wrapper(_run)
sudo = _ignore_timeout_wrapper(_sudo)


def _delete_excess_files(
        init_directory,
        dir_templates=(r'^__pycache__$', r'^[.]cache$', r'^[.]idea$', r'^htmlcov$'),
        file_templates=(r'^[\S]*.pyc$', r'^.coverage$', r'^[\S]*.sqlite3$', r'^[\S]*.cmd', r'^TODO$')
):
    compiled_dir_templates = [re.compile(template) for template in dir_templates]
    compiled_file_templates = [re.compile(template) for template in file_templates]

    for path, dirs, files in os.walk(init_directory, topdown=True):
        for directory in [d for d in dirs if any(template.match(d) for template in compiled_dir_templates)]:
            rmtree(os.path.join(path, directory), ignore_errors=True)

        for file in [f for f in files if any(template.match(f) for template in compiled_file_templates)]:
            try:
                os.remove(os.path.join(path, file))
            except:  # noqa
                pass


def _create_requirements(project_dir, requirements_dir):
    """
    You must resolve conflicts yourself
    """

    result = []
    requirements_template = re.compile(r'.*requirements.txt')

    for path, dirs, files in os.walk(project_dir, topdown=True):
        for file in files:
            if requirements_template.match(file):
                with open(os.path.join(path, file)) as f:
                    result.extend(i.replace(' ', '') for i in f.readlines())
                os.remove(os.path.join(path, file))

    with open(os.path.join(requirements_dir, 'requirements.txt'), 'w') as requirements_file:
        requirements_file.write('\n'.join(set(result)))


def get_project_dir():
    return env['{}_project_dir'.format(env.host_string)]


@load_config('server_config.ini')
def tune_env(config, *_, **__):  # noqa
    for params in config.values():
        host_string = join_host_strings(params['USER'], params['HOST'], params.get('PORT'))
        env.hosts.append(host_string)

        env.connection_attempts = ATTEMPTS

        if env.key_filename is None:
            env.key_filename = [params['KEY_PATH']]
        else:
            env.key_filename.append(params['KEY_PATH'])

        env['{}_project_dir'.format(host_string)] = params['PROJECT_DIR']


def prepare_projects():
    projects_path = os.path.join('deployment_tools', 'src')

    for project_dir in os.listdir(projects_path):
        project_abs_path = os.path.join(projects_path, project_dir)
        if not os.path.isdir(project_abs_path):
            continue

        _create_requirements(project_abs_path, project_abs_path)

    _delete_excess_files('deployment_tools')


def upload_files():
    sudo('mkdir -p {}'.format(get_project_dir()))
    sudo('chmod 777 -R {}'.format(get_project_dir()))
    with cd(get_project_dir()):
        put('deployment_tools/*', './')


def remove_files():
    project_dir = get_project_dir()
    if re.match('^/opt/[a-zA-Z/]*$', project_dir):
        sudo('rm -rf {}'.format(os.path.join(get_project_dir(), '*')))
    else:
        raise Exception


def chmod_opt():
    sudo('chmod 777 -R /opt')


def install_system_dependencies():
    sudo('''yum groupinstall -y development && \\
            yum install -y openssl-devel bzip2-devel wget gcc
    ''')


def install_python():
    with cd('/opt'):
        run('wget http://www.python.org/ftp/python/3.5.2/Python-3.5.2.tar.xz && tar -xvJf Python-3.5.2.tar.xz')
        python_path = '/opt/python'

        with cd('Python-3.5.2'):
            run('./configure --prefix={} && make && make altinstall'.format(python_path))
        run('rm -f Python-3.5.2.tar.xz')


def install_python_dependencies():
    with cd(get_project_dir()):
        run('/opt/python/bin/pip3.5 install -r requirements.txt')


def install_docker():
    with cd(get_project_dir()):
        run('/opt/python/bin/invoke install_docker')


def create_docker_network():
    with cd(get_project_dir()):
        run('/opt/python/bin/invoke create_docker_network')


def prepare_to_start():
    with cd(get_project_dir()):
        run('/opt/python/bin/invoke prepare_files')


def build_services():
    with cd(get_project_dir()):
        run('/opt/python/bin/invoke build_services')


def up_services():
    with cd(get_project_dir()):
        run('/opt/python/bin/invoke up_services')


def chmod_sockets():
    with cd(get_project_dir()):
        run('/opt/python/bin/invoke chmod_sockets')


def ps_services():
    with cd(get_project_dir()):
        run('/opt/python/bin/invoke ps_services')


def down_services():
    with cd(get_project_dir()):
        run('/opt/python/bin/invoke down_services')


def remove_images():
    with cd(get_project_dir()):
        run('/opt/python/bin/invoke remove_images')


@contextmanager
def disconnect():
    yield
    host = env.host_string
    if host and host in connections:
        normalized_host = normalize(host)
        connections[host].get_transport().close()
        connect(normalized_host[0], normalized_host[1], normalized_host[2], HostConnectionCache())


def deploy():
    with disconnect():
        chmod_opt()

        prepare_projects()
        upload_files()

        install_system_dependencies()
        install_python()
        install_python_dependencies()
        install_docker()

    with disconnect():
        create_docker_network()

    prepare_to_start()

    with disconnect():
        build_services()

    with disconnect():
        up_services()
    chmod_sockets()


def force_update():
    with disconnect():
        prepare_projects()

        down_services()
        remove_images()

        remove_files()
        upload_files()

        install_python_dependencies()

    prepare_to_start()

    with disconnect():
        build_services()

    with disconnect():
        up_services()
    chmod_sockets()
