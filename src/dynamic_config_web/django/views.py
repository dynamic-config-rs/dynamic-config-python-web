"""The health, metrics and diagnostics views.

Plain Django views over the shared core, so a project can route them itself:

    from dynamic_config_web.django import views

    urlpatterns = [
        path("healthz", views.healthz),
        path("readyz", views.readyz),
    ]

or take the whole set from :mod:`dynamic_config_web.django.urls`, which is
the shorter road and the one that gets the guard right.

The two diagnostics views are the ones with teeth — they render values — and
they are only reachable when the installation carries a guard. They raise
`Http404` rather than returning 403 for a request the guard refuses, so a
scanner learns nothing from the response either.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.http import Http404, HttpResponse, JsonResponse

from dynamic_config_web._diagnostics import check, explain, never
from dynamic_config_web._health import liveness, readiness
from dynamic_config_web._metrics import CONTENT_TYPE, metrics_body

from . import Installation, installation

if TYPE_CHECKING:  # pragma: no cover - typing only
    from django.http import HttpRequest

__all__ = [
    "check_view",
    "diagnostics_are_available",
    "explain_view",
    "healthz",
    "metrics",
    "readyz",
]


def healthz(request: HttpRequest) -> JsonResponse:
    """The process is up. Configuration has no say in this one.

    Always 200: a process whose configuration is stale should be taken out
    of the load balancer, not restarted into reading the same broken file.
    """
    del request

    report = liveness()

    return JsonResponse(report.body, status=report.status_code)


def readyz(request: HttpRequest) -> JsonResponse:
    """Serving something, and the reloads since have worked."""
    del request

    install = installation()
    report = readiness(*install.wiring.configs, stale_after=install.stale_after)

    return JsonResponse(report.body, status=report.status_code)


def metrics(request: HttpRequest) -> HttpResponse:
    """The engine's series, built per scrape."""
    del request

    install = installation()

    return HttpResponse(
        metrics_body(*install.wiring.configs),
        content_type=CONTENT_TYPE,
    )


def explain_view(request: HttpRequest, path: str) -> HttpResponse:
    """Every layer's answer for one dotted path.

        GET /_config/explain/database.port?config=db

    Synchronous, and re-reads the sources. Under ASGI Django runs a sync
    view in a worker thread, so the loop is not blocked either way — which
    is why this adapter, alone among the seven, has no async twin to keep in
    step.
    """
    install = _permitted(request)

    try:
        config = install.config(request.GET.get("config"))
    except LookupError as unknown:
        return JsonResponse({"detail": str(unknown)}, status=400)

    return HttpResponse(explain(config, path), content_type="text/plain")


def check_view(request: HttpRequest) -> JsonResponse:
    """Would each configuration load, and does anything supply unknown keys."""
    install = _permitted(request)

    return JsonResponse(
        {config.key: check(config) for config in install.wiring.configs}
    )


def diagnostics_are_available(install: Installation) -> bool:
    """Whether this installation has a guard, and so may mount diagnostics.

    The same question :mod:`dynamic_config_web.django.urls` asks before
    building the two patterns, kept here so a project routing the views by
    hand can ask it too.
    """
    return install.guard is not None and install.guard is not never


def _permitted(request: HttpRequest) -> Installation:
    """The installation, if this request is allowed to ask it anything."""
    install = installation()

    if not diagnostics_are_available(install) or not install.guard(request):  # type: ignore[misc]
        raise Http404

    return install
