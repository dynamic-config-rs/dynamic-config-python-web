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

from typing import Any

import django
from django.conf import settings
from django.urls import clear_url_caches

#: What the root URLconf serves. Rewritten by :func:`route`.
urlpatterns: list[Any] = []


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
        INSTALLED_APPS=["rest_framework", "ninja"],
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
