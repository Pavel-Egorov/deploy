python_deploy
=============

> :warning: **Deprecation warning**: this project isn't under support anymore, it may still work for some cases,
> but it isn't guaranteed. 

This package allows you to deploy python web service to remote server using fabric on client side and invoke, docker on 
server side.

Currently it supports:
- Django-based services

---
Installation
------------

Prerequisites:

1. Python 3.5.x
---

1. Download project source files
2. In the terminal type:

        cd path_to_deploy/deploy
        pip3.5 install -r requirements.txt

---
Deploy Your project
-------------------

1. You need server with CentOS 7 and open ssh port
2. Put .pem server ssh certificate to deploy/certs
3. Create server_config.ini in deploy/ (example in deploy/server_config_example.ini)
4. Create deployment.ini in deploy/deployment_tools/ (example in deploy/deployment_tools/deployment_example.ini)
5. Copy Your Django projects to deploy/deployment_tools/src/
6. Type below instructions in terminal (install _GitBash_ on Windows)

        cd path_to_deploy/deploy

    6.1. First deploy:

        fab tune_env deploy

    6.2. Update:

        fab tune_env force_update

---
CONTRIBUTE
----------

If You have found an error or want to offer some changes - create a pull request,
and I will review it as soon as possible!
