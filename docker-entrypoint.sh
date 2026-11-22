#!/bin/sh
set -e
python manage.py migrate --noinput
python manage.py ensure_initial_admin
exec "$@"
