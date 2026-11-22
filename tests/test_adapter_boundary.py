"""ADR 0003: the web and API adapters reach the domain only through the service layer.

import-linter covers `netatlas.**.models`, but it cannot forbid submodules of external
packages such as `django.db`, and it cannot see a model re-exported by a service. This
check reads each adapter's imports and the dotted names used on them, wherever they
appear, including inside functions.

Dynamic access (`getattr`, `importlib`) can't be detected statically and is left to review.
"""

import ast
import importlib.util
import inspect
import pydoc
from pathlib import Path

import pytest
from django.db.models import Model

import netatlas

ADAPTERS = ("web", "api")
FORBIDDEN = (
    "django.db",
    "django.apps",
    "django.contrib.auth.get_user_model",
    # These DRF classes read and write models directly, skipping the services.
    "rest_framework.generics",
    "rest_framework.mixins",
    "rest_framework.serializers.ModelSerializer",
    "rest_framework.serializers.HyperlinkedModelSerializer",
    "rest_framework.viewsets.GenericViewSet",
    "rest_framework.viewsets.ModelViewSet",
    "rest_framework.viewsets.ReadOnlyModelViewSet",
)
PACKAGE_ROOT = Path(netatlas.__file__).parent


def _is_forbidden(path):
    return any(path == name or path.startswith(f"{name}.") for name in FORBIDDEN)


def _is_model(path):
    try:
        found = pydoc.locate(path)
    except pydoc.ErrorDuringImport:
        return False
    return inspect.isclass(found) and issubclass(found, Model)


def _dotted(node, bindings):
    """The full dotted path of `a.b.c`, with `a` expanded to what it was imported as."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name) or node.id not in bindings:
        return None
    return ".".join([bindings[node.id], *reversed(parts)])


def find_bypasses(source, package):
    """Every dotted path in `source` that reaches the database layer or a model directly."""
    tree = ast.parse(source)
    bindings = {}
    paths = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                paths.add(alias.name)
                if alias.asname:
                    bindings[alias.asname] = alias.name
                else:
                    root = alias.name.partition(".")[0]
                    bindings[root] = root
        elif isinstance(node, ast.ImportFrom):
            base = importlib.util.resolve_name("." * node.level + (node.module or ""), package)
            for alias in node.names:
                path = f"{base}.{alias.name}"
                paths.add(path)
                bindings[alias.asname or alias.name] = path

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            path = _dotted(node, bindings)
            if path:
                paths.add(path)

    return sorted(
        path for path in paths if path.endswith(".*") or _is_forbidden(path) or _is_model(path)
    )


def _adapter_modules():
    return [
        module for adapter in ADAPTERS for module in sorted((PACKAGE_ROOT / adapter).rglob("*.py"))
    ]


@pytest.mark.parametrize(
    "module", _adapter_modules(), ids=lambda path: str(path.relative_to(PACKAGE_ROOT))
)
def test_adapter_does_not_bypass_the_service_layer(module):
    relative = module.relative_to(PACKAGE_ROOT.parent).with_suffix("")
    package = ".".join(relative.parts[:-1])

    assert find_bypasses(module.read_text(), package) == []


@pytest.mark.parametrize(
    "source",
    [
        pytest.param("from django.db import connection", id="connection"),
        pytest.param("from django.db import models", id="db-submodule"),
        pytest.param("from django import db", id="db-from-parent"),
        pytest.param("import django.db", id="import-db"),
        pytest.param("import django.db as database", id="import-db-aliased"),
        pytest.param("import django\ndjango.db.connection.cursor()", id="db-by-attribute"),
        pytest.param("from django.apps import apps", id="app-registry"),
        pytest.param("from django import apps", id="apps-from-parent"),
        pytest.param("from django.contrib.auth import get_user_model", id="get-user-model"),
        pytest.param("from django.contrib.auth.backends import Permission", id="reexported-model"),
        pytest.param(
            "from django.contrib.auth.backends import Permission as P", id="reexported-aliased"
        ),
        pytest.param(
            "from django.contrib.auth import backends\nbackends.Permission.objects.all()",
            id="reexported-by-attribute",
        ),
        pytest.param("def view():\n    from django.db import connection", id="inside-function"),
        pytest.param("from django.contrib.auth.backends import *", id="star-import"),
        pytest.param(
            "from rest_framework.serializers import ModelSerializer", id="model-serializer"
        ),
        pytest.param(
            "from rest_framework import serializers\nserializers.ModelSerializer",
            id="model-serializer-by-attribute",
        ),
        pytest.param(
            "from rest_framework.serializers import HyperlinkedModelSerializer",
            id="hyperlinked-model-serializer",
        ),
        pytest.param("from rest_framework.viewsets import ModelViewSet", id="model-viewset"),
        pytest.param(
            "from rest_framework.viewsets import ReadOnlyModelViewSet", id="read-only-viewset"
        ),
        pytest.param("from rest_framework.viewsets import GenericViewSet", id="generic-viewset"),
        pytest.param("from rest_framework import generics", id="generic-views"),
        pytest.param("from rest_framework.generics import ListAPIView", id="generic-list-view"),
        pytest.param("from rest_framework import mixins", id="model-mixins"),
    ],
)
def test_probe_import_is_caught(source):
    assert find_bypasses(source, "netatlas.web")


@pytest.mark.parametrize(
    "source",
    [
        "from django.shortcuts import render",
        "from django.contrib.auth import views",
        "from netatlas.system.services import get_system_status",
        "from . import views",
        "from rest_framework import serializers\nserializers.Serializer",
        "from rest_framework.views import APIView",
    ],
)
def test_legitimate_adapter_import_is_allowed(source):
    assert find_bypasses(source, "netatlas.web") == []
