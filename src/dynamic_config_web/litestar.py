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

from ._asgi import ScopeMiddleware as _SharedScopeMiddleware
from ._diagnostics import Guard
from ._errors import MissingFrameworkError
from ._routes import RouteContext, RouteError, allowed, route_table
from ._scope import current
from ._wiring import Wiring

try:
    from litestar import Response, Router, get
    from litestar.di import Provide
    from litestar.exceptions import HTTPException

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


class ScopeMiddleware(_SharedScopeMiddleware):
    """The shared raw-ASGI scope middleware, under this adapter's name.

    A subclass rather than an alias so the class's qualname says which
    adapter mounted it in a traceback.
    """


def router(
    wiring: Wiring,
    *,
    path: str = "/",
    metrics: bool = True,
    stale_after: Optional[float] = None,
    guard: Optional[Guard] = None,
    diagnostics_path: str = "/_config",
) -> Router:
    """The health, metrics and diagnostics routes, as a Litestar router.

    The routes are the shared table; this function only translates —
    Litestar's `{path:path}` parameter, its `Response`, and its
    `HTTPException` from a refusal.
    """
    table = route_table(
        wiring,
        metrics=metrics,
        stale_after=stale_after,
        guard=guard,
        diagnostics_prefix=diagnostics_path,
    )

    handlers: list[Any] = []

    def make(entry: Any) -> Any:
        route_path = entry.path.replace("{path}", "{path:path}")

        if "{path:path}" in route_path:

            @get(route_path, include_in_schema=False, name=f"config_{entry.name}")
            async def handler(
                request: Request[Any, Any, Any], path: FromPath[str]
            ) -> Response[str]:
                return await _answer(entry, request, path)

        else:

            @get(route_path, include_in_schema=False, name=f"config_{entry.name}")
            async def handler(request: Request[Any, Any, Any]) -> Response[str]:
                return await _answer(entry, request, None)

        return handler

    async def _answer(
        entry: Any, request: Request[Any, Any, Any], raw_path: Optional[str]
    ) -> Response[str]:
        if entry.guarded:
            try:
                allowed(request, guard)
            except RouteError as refused:
                raise HTTPException(
                    status_code=refused.status, detail=refused.detail
                ) from None

        context = RouteContext(
            # A Litestar path parameter arrives with its leading slash.
            path_param=None if raw_path is None else raw_path.lstrip("/"),
            query=request.query_params,
        )

        try:
            reply = await entry.handle_async(context)
        except RouteError as refused:
            raise HTTPException(
                status_code=refused.status, detail=refused.detail
            ) from None

        return Response(
            reply.body, status_code=reply.status, media_type=reply.content_type
        )

    for entry in table:
        handlers.append(make(entry))

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


def plugins(
    target: DynamicConfig[Any] | ConfigGroup | Wiring, **options: Any
) -> Sequence[InitPlugin]:
    """`[DynamicConfigPlugin(...)]`, for a `plugins=` argument.

    A one-element sequence rather than a plugin, because `plugins=` takes a
    list and a caller with no other plugin should not have to write the
    brackets.
    """
    return [DynamicConfigPlugin(target, **options)]
