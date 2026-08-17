"""Django REST Framework: the same surface, inside DRF's own auth.

    from dynamic_config_web.django.drf import urls as config_urls

    urlpatterns = [
        path("internal/", include(config_urls())),
        ...,
    ]

The plain Django views in :mod:`dynamic_config_web.django.views` already
work in a DRF project — they are just views. These exist for the project
that wants the *diagnostics* inside its existing permission scheme rather
than behind a shared token: `ConfigDiagnosticsPermission` consults the
installation's guard, and `permission_classes` on the views below can be
replaced with `IsAdminUser`, a scope check, or anything else DRF offers.

The readiness and metrics views deliberately stay open, because a probe and
a scrape are not authenticated callers — and neither renders a value.

One difference from the plain views, worth knowing before you choose: a
request these refuse gets **403**, where the plain views answer 404. That is
DRF's convention and a project's own permission classes will produce it
anyway, so overriding it here would be the surprising choice. The rule that
matters is unchanged either way — with no guard configured, neither set of
diagnostics routes is built at all.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Optional

from dynamic_config_web._diagnostics import check, explain, never
from dynamic_config_web._errors import MissingFrameworkError
from dynamic_config_web._health import liveness, readiness
from dynamic_config_web._metrics import CONTENT_TYPE, metrics_body

from . import Installation, installation

try:
    from django.http import HttpResponse
    from django.urls import path, re_path
    from rest_framework.permissions import AllowAny, BasePermission
    from rest_framework.response import Response
    from rest_framework.views import APIView
except ImportError as absent:  # pragma: no cover - exercised in a subprocess
    raise MissingFrameworkError("Django REST Framework", "drf") from absent

if TYPE_CHECKING:  # pragma: no cover - typing only
    from django.urls import URLPattern
    from rest_framework.request import Request

__all__ = [
    "ConfigCheckView",
    "ConfigDiagnosticsPermission",
    "ConfigExplainView",
    "ConfigLivenessView",
    "ConfigMetricsView",
    "ConfigReadinessView",
    "urls",
]


class ConfigDiagnosticsPermission(BasePermission):  # type: ignore[misc]
    """Defers to the installation's guard.

    So `guard=token_guard(...)` covers the DRF views as well as the plain
    ones, and a project with real authentication can swap this out for its
    own permission class without touching the views.
    """

    message = "not permitted to read configuration diagnostics"

    def has_permission(self, request: Request, view: Any) -> bool:
        """Whether this request may read configuration back out."""
        del view

        install = installation()
        guard = install.guard

        if guard is None or guard is never:
            return False

        # DRF's `Request` proxies attribute access to the underlying
        # `HttpRequest`, so a guard written against Django's request — or
        # against `.headers`, as `token_guard` is — works unchanged.
        return guard(request)


class ConfigLivenessView(APIView):  # type: ignore[misc]
    """`GET` — the process is up. Always 200."""

    permission_classes: Sequence[Any] = [AllowAny]

    def get(self, request: Request) -> Response:
        """Liveness, which configuration has no say in."""
        del request

        report = liveness()

        return Response(report.body, status=report.status_code)


class ConfigReadinessView(APIView):  # type: ignore[misc]
    """`GET` — serving something, and the reloads since have worked."""

    permission_classes: Sequence[Any] = [AllowAny]

    def get(self, request: Request) -> Response:
        """Readiness, as the shared core computes it."""
        del request

        install = installation()
        report = readiness(*install.wiring.configs, stale_after=install.stale_after)

        return Response(report.body, status=report.status_code)


class ConfigMetricsView(APIView):  # type: ignore[misc]
    """`GET` — the engine's series, in the Prometheus exposition format."""

    permission_classes: Sequence[Any] = [AllowAny]

    def get(self, request: Request) -> HttpResponse:
        """The exposition body, built per scrape.

        A plain `HttpResponse`, not a DRF one: the exposition format is
        text with its own media type, and a renderer that may answer JSON
        would produce a body Prometheus cannot read. DRF passes a
        non-`Response` through `finalize_response` untouched, which is the
        documented way to opt one view out of content negotiation.
        """
        del request

        install = installation()

        return HttpResponse(
            metrics_body(*install.wiring.configs),
            content_type=CONTENT_TYPE,
        )


class ConfigCheckView(APIView):  # type: ignore[misc]
    """`GET` — would each configuration load, and any unknown keys."""

    permission_classes: Sequence[Any] = [ConfigDiagnosticsPermission]

    def get(self, request: Request) -> Response:
        """The check report for every configuration served."""
        del request

        install = installation()

        return Response(
            {config.key: check(config) for config in install.wiring.configs}
        )


class ConfigExplainView(APIView):  # type: ignore[misc]
    """`GET /…/explain/<path>` — every layer's answer for one dotted path."""

    permission_classes: Sequence[Any] = [ConfigDiagnosticsPermission]

    def get(self, request: Request, path: str) -> HttpResponse:
        """The explain table, as text — a plain response, as above."""
        install = installation()

        try:
            config = install.config(request.query_params.get("config"))
        except LookupError as unknown:
            return HttpResponse(str(unknown), status=400, content_type="text/plain")

        return HttpResponse(explain(config, path), content_type="text/plain")


def urls(install: Optional[Installation] = None) -> list[URLPattern]:
    """The DRF views, routed like :func:`dynamic_config_web.django.urls.urls`.

    The diagnostics routes are built only when the installation carries a
    guard — the same rule as everywhere else in this package, for the same
    reason: an unmounted route tells a scanner nothing.
    """
    install = install or installation()

    routes: list[URLPattern] = [
        path("healthz", ConfigLivenessView.as_view(), name="dynamic-config-healthz"),
        path("readyz", ConfigReadinessView.as_view(), name="dynamic-config-readyz"),
    ]

    if install.metrics:
        routes.append(
            path("metrics", ConfigMetricsView.as_view(), name="dynamic-config-metrics")
        )

    if install.guard is not None and install.guard is not never:
        prefix = install.diagnostics_prefix

        routes.extend(
            [
                re_path(
                    rf"^{prefix}/explain/(?P<path>.+)$",
                    ConfigExplainView.as_view(),
                    name="dynamic-config-explain",
                ),
                path(
                    f"{prefix}/check",
                    ConfigCheckView.as_view(),
                    name="dynamic-config-check",
                ),
            ]
        )

    return routes
