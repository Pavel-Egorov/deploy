import configparser
import random
from copy import deepcopy, copy
from functools import wraps
from string import ascii_letters, digits
from uuid import uuid1


def load_config(config_path, target=frozenset(), excess=frozenset()):
    def _load_config(func):
        @wraps(func)
        def _(ctx=None, *args, **kwargs):
            nonlocal target

            parser = configparser.ConfigParser()
            with open(config_path) as config_file:
                parser.read_file(config_file)

            target = {i.lower() for i in target} or {i.lower() for i in parser.keys()}
            target = target.difference({i.lower() for i in {'default'}.union(excess)})

            config = {name: {
                k.upper(): v for k, v in dict(section).items()
            } for name, section in parser.items() if name in target}

            common_params = config.pop('common', {})
            prepared_config = deepcopy(config)

            for section_name, section in config.items():
                params = copy(common_params)
                params.update(section)
                prepared_config[section_name] = normalize(params, {'false': False, 'true': True})

            for section_name, params in config.items():
                prepared_config[section_name].update(normalize(
                    {k: v for k, v in config.get(params.pop('PARENT', None), {}).items() if k not in params},
                    {'false': False, 'true': True}
                ))

            return func(prepared_config, ctx, *args, **kwargs)
        return _

    return _load_config


def get_extra_envs():
    return {
        'SECRET_KEY': ''.join([random.SystemRandom().choice(ascii_letters + digits) for _ in range(50)])
    }


def get_prepared_params(params):
    return '-e ' + ' -e '.join('{}="{}"'.format(name, value) for name, value in params.items())


def create_init_db_file(init_db_envs):
    init_db_script = '#!/usr/bin/env bash\n'

    for unique_id in init_db_envs:
        init_db_script += INIT_DATABASE_TEMPLATE.format(
            database_name_env='{' + DB_NAME_ENV + unique_id + '}',
            user_name_env='{' + DB_USER_NAME_ENV + unique_id + '}',
            user_password_env='{' + DB_USER_PASSWORD_ENV + unique_id + '}'
        )

    with open('init_db.sh', 'w') as f:
        f.write(init_db_script)


def get_init_db_envs(config):
    envs = {}

    for params in config.values():
        unique_id = '_' + uuid1().hex.upper()
        envs[unique_id] = {
            DB_NAME_ENV + unique_id: params[DB_NAME_ENV],
            DB_USER_NAME_ENV + unique_id: params[DB_USER_NAME_ENV],
            DB_USER_PASSWORD_ENV + unique_id: params[DB_USER_PASSWORD_ENV]
        }

    return envs


def normalize(params, translations):
    return {k: translations.get(v, v) for k, v in params.items()}


INIT_DATABASE_TEMPLATE = '''
psql \\
    -v ON_ERROR_STOP=1 \\
    -U "$POSTGRES_USER" \\
    -c "DO \\$\\$ \\
        BEGIN \\
           IF NOT EXISTS ( \\
              SELECT * FROM pg_catalog.pg_user WHERE usename = '${user_name_env}' \\
           ) THEN CREATE USER ${user_name_env} WITH password '${user_password_env}'; \\
           END IF; \\
        END\\$\\$;"

psql \\
    -v ON_ERROR_STOP=1 \\
    -U "$POSTGRES_USER" \\
    -tc "SELECT 1 FROM pg_database WHERE datname = '${database_name_env}'" \\
        | grep -q 1 \\
        || psql -U postgres -c "CREATE DATABASE ${database_name_env}"

psql \\
    -v ON_ERROR_STOP=1 \\
    -U "$POSTGRES_USER" \\
    -c "GRANT ALL privileges ON DATABASE ${database_name_env} TO ${user_name_env};"
'''

DB_NAME_ENV = 'DATABASE_NAME'

DB_USER_NAME_ENV = 'DATABASE_USER_NAME'

DB_USER_PASSWORD_ENV = 'DATABASE_PASSWORD'
