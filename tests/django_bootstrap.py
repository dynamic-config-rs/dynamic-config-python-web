"""One Django configuration for the whole test session.

Django settings can be configured once per process, and `django.setup()` run
once — which is the whole difficulty of testing a Django integration next to
six others in one pytest run. So this module is both the settings and the
root URLconf: it configures Django on first use, and exposes a
`urlpatterns` list the drivers rewrite per case.

The rewrite is why `clear_url_caches()` is called: Django memoises the
resolver for a URLconf, and a list mutated behind its back would otherwise
route to the previous case's views.
"""

from __future__ import annotations

import importlib.util
from typing import Any

import django
from django.conf import settings
from django.urls import clear_url_caches

#: What the root URLconf serves. Rewritten by :func:`route`.
urlpatterns: list[Any] = []


def _installed(*apps: str) -> list[str]:
    """The subset of `apps` this interpreter can actually import."""
    return [app for app in apps if importlib.util.find_spec(app) is not None]


def bootstrap() -> None:
    """Configures Django, once per process."""
    if settings.configured:
        return

    settings.configure(
        DEBUG=False,
        SECRET_KEY="dynamic-config-conformance",
        ALLOWED_HOSTS=["*"],
        ROOT_URLCONF=__name__,
        # Deliberately without `dynamic_config_web.django`: the app's
        # `ready()` reads `DYNAMIC_CONFIG`, and each case wires a different
        # configuration. `tests/test_django_app.py` exercises that path in a
        # subprocess, where a settings module can name one target.
        # Only the ones actually installed. Each CI row installs a single
        # extra — `[drf]` brings `rest_framework`, `[ninja]` brings `ninja`,
        # `[django]` brings neither — and naming an absent app here fails
        # `django.setup()` for every Django case in that row, not just the
        # one that needs it. A developer with all three installed would
        # never see it.
        INSTALLED_APPS=_installed("rest_framework", "ninja"),
        MIDDLEWARE=[
            "dynamic_config_web.django.middleware.DynamicConfigMiddleware",
        ],
        DATABASES={},
        USE_TZ=True,
        LOGGING_CONFIG=None,
        REST_FRAMEWORK={"UNAUTHENTICATED_USER": None},
    )
    django.setup()


def route(patterns: list[Any]) -> None:
    """Replaces what the root URLconf serves."""
    urlpatterns[:] = patterns
    clear_url_caches()
