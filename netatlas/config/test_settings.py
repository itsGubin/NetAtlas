"""Settings for the test suite: development defaults, but a real Postgres like production."""

import os

os.environ.setdefault("NETATLAS_DEBUG", "true")

from netatlas.config.settings import *  # noqa: E402,F403

# Tests render templates without running collectstatic first.
STORAGES = {**STORAGES, "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}}  # noqa: F405
