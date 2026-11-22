FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    NETATLAS_STATIC_ROOT=/app/staticfiles \
    NETATLAS_MEDIA_ROOT=/data/media

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl gettext \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 1000 --home /app netatlas \
    && mkdir -p /data/media && chown netatlas /data/media

WORKDIR /app
COPY pyproject.toml ./
COPY netatlas ./netatlas
COPY manage.py docker-entrypoint.sh docker-healthcheck.sh ./
RUN pip install . \
    && NETATLAS_SECRET_KEY=build-only python manage.py collectstatic --noinput \
    && NETATLAS_SECRET_KEY=build-only python manage.py compilemessages --ignore=.venv

USER netatlas
EXPOSE 8000
VOLUME ["/data/media"]
HEALTHCHECK --interval=10s --timeout=5s --start-period=20s --retries=3 \
    CMD ["./docker-healthcheck.sh"]
ENTRYPOINT ["./docker-entrypoint.sh"]
CMD ["gunicorn", "netatlas.config.wsgi", "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "120", "--no-control-socket"]
