#!/bin/sh
# 웹 서버 시작: 표를 만들고(마이그레이션) gunicorn을 띄운다.
set -e
python manage.py migrate --noinput
exec gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 2 --timeout 60 --access-logfile -
