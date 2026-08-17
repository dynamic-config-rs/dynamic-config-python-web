"""django-ninja: the same surface, as a `Router`.

    # settings.py
    DYNAMIC_CONFIG = {
        "target": "myproject.config:database",
        "guard": "myproject.config:only_operators",
    }

    # urls.py
    from ninja import NinjaAPI
    from dynamic_config_web.django.ninja import router

    api = NinjaAPI()
    api.add_router("/", router())

    urlpatterns = [path("api/", api.urls)]

django-ninja is a Django application, so the lifecycle and the request scope
are already in place: `dynamic_config_web.django` loads and watches in
`AppConfig.ready`, and `DynamicConfigMiddleware` opens the scope. What is
left is routing, which is what this module supplies.

Read configuration in an operation with `snapshot()`, exactly as in a plain
view:

    from dynamic_config_web.django import snapshot

    @api.get("/")
    def index(request):
        db = snapshot()
        return {"host": db.host, "pool": db.pool.max_size}

A refused diagnostics request gets **401** here, where the plain views answer
404 and DRF answers 403. Each follows its own framework's convention; what
does not change is that the diagnostics routes are registered only when a
guard is configured.

`router()` reads the installation twice: once when it is called, to decide
which operations exist at all, and again inside each operation. The second
read is what keeps a mounted router honest — a `configure()` that revokes
the guard, or a `reset()`, takes effect on the next request rather than
being frozen into the routes at import time.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from dynamic_config_web._diagnostics import check, explain
from dynamic_config_web._errors import MissingFrameworkError
from dynamic_config_web._health import liveness, readiness
from dynamic_config_web._metrics import CONTENT_TYPE, metrics_body

from . import Installation, installation
from .views import diagnostics_are_available

try:
    from django.http import Http404, HttpResponse, JsonResponse
    from ninja import Router
except ImportError as absent:  # pragma: no cover - exercised in a subprocess
    raise MissingFrameworkError("django-ninja", "ninja") from absent

if TYPE_CHECKING:  # pragma: no cover - typing only
    from django.http import HttpRequest

__all__ = ["router"]


def _guard_now(request: HttpRequest) -> bool:
    """Whether this request may read configuration back out, asked now.

    django-ninja calls this as the operation's `auth=`. It re-reads the
    installation on every request rather than closing over the guard the
    router was built with, so revoking one takes effect immediately —
    which is what the plain views and the DRF permission both do.
    """
    install = installation()

    return diagnostics_are_available(install) and bool(install.guard(request))  # type: ignore[misc]


def router(install: Optional[Installation] = None) -> Router:
    """The health, metrics and diagnostics operations, as a `Router`.

    Parameters:
        install: the installation whose *shape* decides which operations
            exist — whether `/metrics` is mounted, and whether the
            diagnostics pair is. `None` reads the one this process was
            configured with, so call this from `ready()` or after it; a
            `urls.py` imported before Django has started should pass one.

    Every operation re-reads the installation when it runs, so what a
    request sees is the current one rather than the one this router was
    built from.
    """
    install = install or installation()
    routes = Router()

    # `auth=None` on every operation below, and not by omission: a project
    # that builds `NinjaAPI(auth=django_auth)` sets a default for the whole
    # API, and a liveness probe that has to authenticate reports the auth
    # backend's health rather than the process's.
    @routes.get("/healthz", auth=None, include_in_schema=False)
    def healthz(request: HttpRequest) -> HttpResponse:
        """The process is up. Configuration has no say in this one."""
        del request

        report = liveness()

        return JsonResponse(report.body, status=report.status_code)

    @routes.get("/readyz", auth=None, include_in_schema=False)
    def readyz(request: HttpRequest) -> HttpResponse:
        """Serving something, and the reloads since have worked."""
        del request

        current = installation()
        report = readiness(*current.wiring.configs, stale_after=current.stale_after)

        return JsonResponse(report.body, status=report.status_code)

    if install.metrics:

        @routes.get("/metrics", auth=None, include_in_schema=False)
        def prometheus(request: HttpRequest) -> HttpResponse:
            """The engine's series, built per scrape."""
            del request

            return HttpResponse(
                metrics_body(*installation().wiring.configs),
                content_type=CONTENT_TYPE,
            )

    if diagnostics_are_available(install):
        prefix = install.diagnostics_prefix

        @routes.get(
            f"/{prefix}/explain/{{path:dotted}}",
            auth=_guard_now,
            include_in_schema=False,
        )
        def explain_path(request: HttpRequest, dotted: str) -> HttpResponse:
            """Every layer's answer for one dotted path."""
            current = installation()

            # The guard was revoked between building this router and now.
            # `auth=` already refused, but a router mounted on a second
            # `NinjaAPI` may not have been rebuilt — answer as the plain
            # views would.
            if not diagnostics_are_available(current):
                raise Http404

            try:
                config = current.config(request.GET.get("config"))
            except LookupError as unknown:
                return JsonResponse({"detail": str(unknown)}, status=400)

            return HttpResponse(explain(config, dotted), content_type="text/plain")

        @routes.get(f"/{prefix}/check", auth=_guard_now, include_in_schema=False)
        def check_all(request: HttpRequest) -> HttpResponse:
            """Would each configuration load, and any unknown keys."""
            del request

            current = installation()

            if not diagnostics_are_available(current):
                raise Http404

            return JsonResponse(
                {config.key: check(config) for config in current.wiring.configs}
            )

    return routes
