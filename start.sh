#!/usr/bin/env bash
set -o errexit

python manage.py migrate --noinput

# Bootstrap opcional de superusuario en entornos sin acceso a shell (ej. Render free).
if [[ -n "${DJANGO_SUPERUSER_USERNAME:-}" && -n "${DJANGO_SUPERUSER_EMAIL:-}" && -n "${DJANGO_SUPERUSER_PASSWORD:-}" ]]; then
python manage.py shell <<'PY'
import os
from django.contrib.auth import get_user_model

User = get_user_model()
username = os.environ["DJANGO_SUPERUSER_USERNAME"]
email = os.environ["DJANGO_SUPERUSER_EMAIL"]
password = os.environ["DJANGO_SUPERUSER_PASSWORD"]

user, created = User.objects.get_or_create(
    username=username,
    defaults={"email": email, "is_staff": True, "is_superuser": True},
)

dirty = False
if user.email != email:
    user.email = email
    dirty = True
if not user.is_staff:
    user.is_staff = True
    dirty = True
if not user.is_superuser:
    user.is_superuser = True
    dirty = True

user.set_password(password)
if dirty:
    user.save()
else:
    user.save(update_fields=["password"])

print(f"[bootstrap] superuser listo: {username}")
PY
fi

gunicorn config.wsgi:application --bind 0.0.0.0:${PORT:-10000} --workers 2 --timeout 120
