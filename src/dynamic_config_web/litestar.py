"""Litestar: a plugin, because that is what Litestar gives a library.

    from litestar import Litestar, get
    from dynamic_config import DynamicConfig
    from dynamic_config_web.litestar import DynamicConfigPlugin, NamedDependency

    config = DynamicConfig(Database, key="db").file("config.toml")

    @get("/")
    async def index(db: NamedDependency[Database]) -> dict[str, object]:
        return {"host": db.host}

    app = Litestar([index], plugins=[DynamicConfigPlugin(config)])

The dependency arrives **by name**: the plugin registers a provider under
`dependency_key` — the configuration's own key by default — and a handler
that declares a parameter of that name is given the model this request
began with.

`NamedDependency[...]` is Litestar's own marker, re-exported here so one
import line covers both halves. A bare `db: Database` still resolves, but
Litestar 2.23 and later warn about the inferred form and Litestar 3 will
stop supporting it; on an older Litestar this name is a no-op annotation, so
the same handler works either way.

One `Provide` detail is load-bearing: `use_cache` stays `False`. Litestar's
cache is per *application*, not per request, so a cached provider would pin
one snapshot for the life of the process — the exact bug this package
exists to prevent. Litestar already calls a provider once per request
without it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, Optional

from ._diagnostics import Guard, check_async, explain_async, never
from ._errors import MissingFrameworkError
from ._health import liveness, readiness
from ._metrics import CONTENT_TYPE, metrics_body
from ._scope import current, enter, leave
from ._wiring import Wiring

try:
    from litestar import Response, Router, get
    from litestar.di import Provide
    from litestar.exceptions import HTTPException, NotFoundException

    # `FromPath` rather than a bare `path: str`: the inferred style is
    # deprecated in 2.x and gone in 3, and an adapter must not be the
    # reason a user's own suite prints a deprecation warning.
    from litestar.params import FromPath
    from litestar.plugins import InitPlugin
except ImportError as absent:  # pragma: no cover - exercised in a subprocess
    raise MissingFrameworkError("Litestar", "litestar") from absent

try:
    # 2.23 and later. `NamedDependency[Database]` is `Annotated[Database,
    # Dependency(kind="named")]` — the parameter's own name is the key.
    from litestar.di import NamedDependency
except ImportError:  # pragma: no cover - Litestar < 2.23
    from typing import Annotated, TypeVar

    _Model = TypeVar("_Model")
    # A no-op marker: an older Litestar infers by name anyway and ignores
    # metadata it does not recognise, so a handler written against the new
    # name keeps working on the old framework.
    NamedDependency = Annotated[_Model, "dynamic-config: named dependency"]  # type: ignore[misc]

if TYPE_CHECKING:  # pragma: no cover - typing only
    from litestar import Litestar
    from litestar.config.app import AppConfig
    from litestar.connection import Request
    from litestar.types import Receive, Scope, Send

    from dynamic_config import ConfigGroup, DynamicConfig

__all__ = [
    "DynamicConfigPlugin",
    "NamedDependency",
    "plugins",
    "provide_config",
    "router",
]


def provide_config(config: DynamicConfig[Any]) -> Provide:
    """A `Provide` that yields this configuration's request-scoped model.

        Litestar([index], dependencies={"db": provide_config(config)})

    `sync_to_thread=False` because the provider is a dictionary lookup, and
    `use_cache=False` because Litestar's cache outlives the request — see
    the module docstring.
    """

    def dependency() -> Any:
        return current(config)

    dependency.__name__ = f"{config.key}_config"

    return Provide(dependency, sync_to_thread=False, use_cache=False)


class ScopeMiddleware:
    """Opens one request scope per request, in raw ASGI.

    Litestar's `AbstractMiddleware` would serve as well, but raw ASGI is
    the same thing without a base class and is what the other adapters
    use — one shape to review rather than seven.
    """

    def __init__(self, app: Any, wiring: Wiring) -> None:
        self.app = app
        self.wiring = wiring

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # `str(...)`, because Litestar types the key as its own enum and
        # the value on the wire is the plain ASGI string either way.
        if str(scope["type"]) != "http":
            await self.app(scope, receive, send)

            return

        token = enter(self.wiring.configs)

        try:
            await self.app(scope, receive, send)
        finally:
            leave(token)


def router(
    wiring: Wiring,
    *,
    path: str = "/",
    metrics: bool = True,
    stale_after: Optional[float] = None,
    guard: Optional[Guard] = None,
    diagnostics_path: str = "/_config",
) -> Router:
    """The health, metrics and diagnostics routes, as a Litestar router."""

    @get("/healthz", include_in_schema=False, sync_to_thread=False)
    def healthz() -> Response[Any]:
        """The process is up. Configuration has no say in this one."""
        report = liveness()

        return Response(report.body, status_code=report.status_code)

    @get("/readyz", include_in_schema=False, sync_to_thread=False)
    def readyz() -> Response[Any]:
        """Serving something, and the reloads since have worked."""
        report = readiness(*wiring.configs, stale_after=stale_after)

        return Response(report.body, status_code=report.status_code)

    handlers: list[Any] = [healthz, readyz]

    if metrics:

        @get("/metrics", include_in_schema=False, sync_to_thread=False)
        def prometheus() -> Response[str]:
            """The engine's series, built per scrape."""
            return Response(
                metrics_body(*wiring.configs),
                media_type=CONTENT_TYPE,
            )

        handlers.append(prometheus)

    if guard is not None and guard is not never:

        @get(
            f"{diagnostics_path}/explain/{{path:path}}",
            include_in_schema=False,
        )
        async def explain_path(
            request: Request[Any, Any, Any], path: FromPath[str]
        ) -> Response[str]:
            """Every layer's answer for one dotted path."""
            _allowed(request, guard)

            config = _named(wiring, request.query_params.get("config"))
            # A path parameter arrives with its leading slash.
            dotted = path.lstrip("/")

            return Response(
                await explain_async(config, dotted), media_type="text/plain"
            )

        @get(f"{diagnostics_path}/check", include_in_schema=False)
        async def check_all(request: Request[Any, Any, Any]) -> Response[Any]:
            """Would each configuration load, and any unknown keys."""
            _allowed(request, guard)

            return Response(
                {config.key: await check_async(config) for config in wiring.configs}
            )

        handlers.extend([explain_path, check_all])

    return Router(path=path, route_handlers=handlers)


class DynamicConfigPlugin(InitPlugin):
    """Everything an application needs, added to its `AppConfig`.

        app = Litestar([index], plugins=[DynamicConfigPlugin(config)])

    Four things, all through the seam Litestar provides for exactly this:
    a lifespan that loads and watches, a dependency under
    `dependency_key`, the health routes, and the request-scope middleware.
    """

    def __init__(
        self,
        target: DynamicConfig[Any] | ConfigGroup | Wiring,
        *,
        watch: bool = True,
        debounce: float = 0.25,
        poll_interval: Optional[float] = None,
        request_scope: bool = True,
        routes: bool = True,
        path: str = "/",
        metrics: bool = True,
        stale_after: Optional[float] = None,
        guard: Optional[Guard] = None,
        diagnostics_path: str = "/_config",
        dependency_key: Optional[str] = None,
    ) -> None:
        """Builds the plugin; `on_app_init` is what installs it.

        Parameters:
            target: a configuration, a `ConfigGroup`, or a `Wiring` already
                built — which is what two applications sharing a lifecycle
                pass, and what a test that inspects the lifecycle passes.
            dependency_key: the parameter name a handler declares to be
                given the model. `None` uses each configuration's own key,
                which is what a group wants; a single configuration named
                `""` (a whole-document one) falls back to `config`.

        The rest are the arguments every adapter here takes; see
        :func:`dynamic_config_web.fastapi.setup`.
        """
        self.wiring = (
            target
            if isinstance(target, Wiring)
            else Wiring(
                target, watch=watch, debounce=debounce, poll_interval=poll_interval
            )
        )
        self._request_scope = request_scope
        self._routes = routes
        self._path = path
        self._metrics = metrics
        self._stale_after = stale_after
        self._guard = guard
        self._diagnostics_path = diagnostics_path
        self._dependency_key = dependency_key

    def on_app_init(self, app_config: AppConfig) -> AppConfig:
        """Adds the lifespan, the dependency, the routes and the middleware."""
        app_config.lifespan.append(self._lifespan)

        for config in self.wiring.configs:
            key = self._dependency_key or config.key or "config"
            app_config.dependencies[key] = provide_config(config)

        if self._routes:
            app_config.route_handlers.append(
                router(
                    self.wiring,
                    path=self._path,
                    metrics=self._metrics,
                    stale_after=self._stale_after,
                    guard=self._guard,
                    diagnostics_path=self._diagnostics_path,
                )
            )

        if self._request_scope:
            app_config.middleware.append(self._middleware)

        return app_config

    @asynccontextmanager
    async def _lifespan(self, app: Litestar) -> AsyncIterator[None]:
        """Load and watch for the length of the application."""
        del app

        await self.wiring.start_async()

        try:
            yield
        finally:
            self.wiring.stop()

    def _middleware(self, app: Any) -> ScopeMiddleware:
        """The scope middleware, bound to this plugin's wiring."""
        return ScopeMiddleware(app, self.wiring)


def _allowed(request: Request[Any, Any, Any], guard: Guard) -> None:
    """Refuses a request the guard does not accept."""
    if not guard(request):
        raise NotFoundException()


def _named(wiring: Wiring, key: Optional[str]) -> Any:
    """The configuration a diagnostics request names, or the only one."""
    try:
        return wiring.config(key)
    except LookupError as unknown:
        raise HTTPException(status_code=400, detail=str(unknown)) from unknown


def plugins(
    target: DynamicConfig[Any] | ConfigGroup | Wiring, **options: Any
) -> Sequence[InitPlugin]:
    """`[DynamicConfigPlugin(...)]`, for a `plugins=` argument.

    A one-element sequence rather than a plugin, because `plugins=` takes a
    list and a caller with no other plugin should not have to write the
    brackets.
    """
    return [DynamicConfigPlugin(target, **options)]
