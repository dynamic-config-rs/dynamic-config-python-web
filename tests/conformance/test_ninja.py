"""django-ninja, against the shared contract.

Django's driver with a different route builder, exactly as the DRF one is:
the lifecycle and the request scope are the Django adapter's, and what
changes is only who answers.
"""

from __future__ import annotations

import importlib.util
from typing import Any, Callable

import pytest

# `find_spec` rather than `importorskip`, which would *import* django-ninja —
# and it reads `settings.NINJA_*` the moment it is imported, before the
# bootstrap below has configured any.
if importlib.util.find_spec("ninja") is None:  # pragma: no cover
    pytest.skip("django-ninja is not installed", allow_module_level=True)

import django_bootstrap

django_bootstrap.bootstrap()

from ninja import NinjaAPI  # noqa: E402

from dynamic_config_web import Wiring  # noqa: E402
from dynamic_config_web.django.ninja import router as ninja_router  # noqa: E402

from . import suite  # noqa: E402
from .test_django import DjangoDriver, _record  # noqa: E402,F401


def _mounted(install: Any) -> list[Any]:
    """The router, behind a `NinjaAPI`, as URL patterns.

    A fresh `NinjaAPI` per case: django-ninja registers its namespace
    globally, and reusing one across twelve applications would carry the
    previous case's routes into the next.
    """
    from django.urls import path

    api = NinjaAPI(urls_namespace=f"dynamic-config-{id(install)}")
    api.add_router("/", ninja_router(install))

    return [path("", api.urls)]


class NinjaDriver(DjangoDriver):
    """Django's driver, with django-ninja's operations doing the answering."""

    name = "ninja"
    routes = staticmethod(_mounted)


@pytest.mark.usefixtures("_record")
@pytest.mark.parametrize("case", suite.cases(NinjaDriver()), ids=suite.name_of)
def test_ninja_conformance(
    case: Callable[..., None],
    config_file: Any,
    dynamic_config_wiring: Callable[..., Wiring],
    config: Any,
) -> None:
    """Every case in the shared contract, against django-ninja."""
    wiring = dynamic_config_wiring(
        config, watch=suite.needs_watching(case), start=False
    )

    case(NinjaDriver(), wiring, config_file)
