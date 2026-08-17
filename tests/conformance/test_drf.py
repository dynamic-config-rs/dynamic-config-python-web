"""Django REST Framework, against the shared contract.

The same driver as Django's, routed through the DRF views instead — which
is the point of the DRF half: a different way to reach the same surface, so
its diagnostics can live inside a project's own permission scheme. If the
two ever answered differently, this file is where it would show.
"""

from __future__ import annotations

from typing import Any, Callable

import pytest

pytest.importorskip("rest_framework")

import django_bootstrap

# Before the DRF import, not after: `rest_framework.views` reads
# `settings.REST_FRAMEWORK` at import time, so a DRF project has settings
# configured long before anything imports it. Here that has to be arranged.
django_bootstrap.bootstrap()

from dynamic_config_web import Wiring  # noqa: E402
from dynamic_config_web.django.drf import urls as drf_urls  # noqa: E402

from . import suite  # noqa: E402
from .test_django import DjangoDriver, _record  # noqa: E402,F401


class DRFDriver(DjangoDriver):
    """Django's driver, with DRF's `APIView`s doing the answering."""

    name = "drf"
    routes = staticmethod(drf_urls)


@pytest.mark.usefixtures("_record")
@pytest.mark.parametrize("case", suite.cases(DRFDriver()), ids=suite.name_of)
def test_drf_conformance(
    case: Callable[..., None],
    config_file: Any,
    dynamic_config_wiring: Callable[..., Wiring],
    config: Any,
) -> None:
    """Every case in the shared contract, against DRF."""
    wiring = dynamic_config_wiring(
        config, watch=suite.needs_watching(case), start=False
    )

    case(DRFDriver(), wiring, config_file)
