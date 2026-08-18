"""FastAPI: the configuration as a dependency, the watcher as a lifespan.

    from fastapi import Depends, FastAPI
    from dynamic_config import DynamicConfig
    from dynamic_config_web.fastapi import setup, config_dependency

    config = DynamicConfig(Database, key="db").file("config.toml").env("APP_")
    app = FastAPI()

    setup(app, config)
    database = config_dependency(config)

    @app.get("/")
    def index(db: Database = Depends(database)):
        return {"host": db.host}

`setup` does four things, and each of them is a mistake somebody has
already made by hand:

- **loads and watches inside the app's lifespan**, wrapping whatever
  lifespan the application already had. One place, paired with shutdown,
  so `uvicorn --reload` rebuilding the app and a test suite building a
  client per test both stop the previous watcher before starting the
  next — a second watcher on one configuration is `AlreadyExists`,
  deliberately.
- **opens a request scope** in an ASGI middleware, so `config_dependency`
  hands every line of a handler the same model however many times it
  asks.
- **mounts `/healthz`, `/readyz` and `/metrics`**, which are three
  different questions and are answered as such.
- **mounts `/_config/explain` and `/_config/check` only when given a
  guard**, because a route that can print configuration is a decision.
"""

from __future__ import annotations

import weakref
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, Callable, Optional, TypeVar

from ._asgi import ScopeMiddleware as _SharedScopeMiddleware
from ._diagnostics import Guard
from ._errors import MissingFrameworkError
from ._routes import RouteContext, RouteError, allowed, route_table
from ._scope import current
from ._wiring import Wiring

try:
    from fastapi import APIRouter, HTTPException, Request, Response
except ImportError as absent:  # pragma: no cover - exercised in a subprocess
    raise MissingFrameworkError("FastAPI", "fastapi") from absent

if TYPE_CHECKING:  # pragma: no cover - typing only
    from fastapi import FastAPI

    from dynamic_config import ConfigGroup, DynamicConfig

M = TypeVar("M")

__all__ = ["config_dependency", "lifespan", "router", "setup"]

#: One dependency callable per configuration, so
#: `app.dependency_overrides[config_dependency(config)] = …` works from a
#: test that never saw the object the application built. Weak, because the
#: dependency belongs to the configuration and neither should outlive it.
_DEPENDENCIES: weakref.WeakKeyDictionary[Any, Callable[[], Any]] = (
    weakref.WeakKeyDictionary()
)


def config_dependency(config: DynamicConfig[M]) -> Callable[[], M]:
    """The dependency that yields this configuration's model.

        database = config_dependency(config)

        @app.get("/")
        def index(db: Database = Depends(database)):
            ...

    **The same object every time**, which is what makes the override work:

        app.dependency_overrides[config_dependency(config)] = lambda: Database(...)

    A plain `def` rather than `async def`, deliberately: FastAPI runs a
    `def` dependency on a worker thread and an `async def` one on the
    loop, and this is a dictionary lookup either way — being sync means a
    sync endpoint does not pay for a loop round trip. The request scope
    reaches the worker thread because `contextvars` is what the threadpool
    copies.
    """
    made = _DEPENDENCIES.get(config)

    if made is not None:
        return made

    def dependency() -> M:
        return current(config)

    # The name a dependency override error message prints, and what shows
    # up in a stack trace: `dependency` says nothing, `db_config` does.
    dependency.__name__ = f"{config.key}_config"
    dependency.__qualname__ = dependency.__name__
    _DEPENDENCIES[config] = dependency

    return dependency


@asynccontextmanager
async def lifespan(
    wiring: Wiring, app: Optional[FastAPI] = None
) -> AsyncIterator[None]:
    """The load-watch-stop block, for an application that builds its own.

        @asynccontextmanager
        async def lifespan(app):
            async with dynamic_config_web.fastapi.lifespan(wiring):
                yield

    `setup` wraps this around the app's existing lifespan for you; this is
    the same thing for somebody who would rather compose it themselves.
    """
    del app

    await wiring.start_async()

    try:
        yield
    finally:
        # Stopping needs no await: it drops the notification backend and
        # returns without joining the watcher thread or waiting out a
        # debounce window.
        wiring.stop()


# The raw-ASGI scope middleware lives in `_asgi` now — FastAPI and
# Litestar mount the same fifteen lines, so the lines exist once.
_ScopeMiddleware = _SharedScopeMiddleware


def router(
    wiring: Wiring,
    *,
    prefix: str = "",
    metrics: bool = True,
    stale_after: Optional[float] = None,
    guard: Optional[Guard] = None,
    diagnostics_prefix: str = "/_config",
) -> APIRouter:
    """The health, metrics and diagnostics routes, as a router to mount.

    `setup` includes this; taking it directly is for an application that
    wants them under its own prefix, behind its own dependencies, or in a
    sub-application it mounts elsewhere.

    The routes themselves are the shared table — nine adapters, one
    definition — and this function is only the translation into FastAPI:
    a path parameter for `{path}`, `Response` from a `Reply`, and
    `HTTPException` from a refusal.
    """
    routes = APIRouter(prefix=prefix)

    table = route_table(
        wiring,
        metrics=metrics,
        stale_after=stale_after,
        guard=guard,
        diagnostics_prefix=diagnostics_prefix,
    )

    for route in table:
        mount = route.path.replace("{path}", "{path:path}")

        def make(entry: Any) -> Callable[..., Any]:
            async def endpoint(request: Request, path: str = "") -> Response:
                if entry.guarded:
                    try:
                        allowed(request, guard)
                    except RouteError as refused:
                        raise HTTPException(
                            status_code=refused.status, detail=refused.detail
                        ) from None

                context = RouteContext(
                    path_param=path or None, query=request.query_params
                )

                try:
                    reply = await entry.handle_async(context)
                except RouteError as refused:
                    raise HTTPException(
                        status_code=refused.status, detail=refused.detail
                    ) from None

                return Response(
                    content=reply.body,
                    status_code=reply.status,
                    media_type=reply.content_type,
                )

            return endpoint

        routes.get(mount, include_in_schema=False, name=route.name)(make(route))

    return routes


def setup(
    app: FastAPI,
    target: DynamicConfig[Any] | ConfigGroup | Wiring,
    *,
    watch: bool = True,
    debounce: float = 0.25,
    poll_interval: Optional[float] = None,
    request_scope: bool = True,
    routes: bool = True,
    prefix: str = "",
    metrics: bool = True,
    stale_after: Optional[float] = None,
    guard: Optional[Guard] = None,
    diagnostics_prefix: str = "/_config",
) -> Wiring:
    """Wires `target` into `app`, and answers the wiring.

    Parameters:
        app: the application. Its lifespan is *wrapped*, not replaced.
        target: one `DynamicConfig`, a `ConfigGroup` of them, or a
            :class:`~dynamic_config_web.Wiring` already built — which is
            what a test that wants to inspect the lifecycle passes, and
            what two applications sharing one lifecycle pass. A wiring
            brings its own options, so `watch`, `debounce` and
            `poll_interval` are ignored when one is given.
        watch: start a watcher. `False` still loads — the right answer
            for a serverless function, where nothing is long-lived enough
            to watch.
        debounce: seconds to wait after a change before reloading.
        poll_interval: seconds between polls, for a filesystem that
            delivers no events — a container bind mount, a network share.
        request_scope: install the middleware that gives each request one
            reading of the configuration. Turning it off means
            `config_dependency` raises, and is for an application that
            opens its own scope.
        routes: mount `/healthz`, `/readyz` and `/metrics`.
        prefix: where to mount them.
        metrics: mount `/metrics` as well as the health pair.
        stale_after: seconds since the last install after which `/readyz`
            reports a configuration as stale. `None` never does.
        guard: who may call the diagnostics routes — a callable taking
            the request and answering whether it may. Without one they
            are **not mounted at all**, so an unguarded service has
            nothing there to find; a guarded one answers 403 to a request
            the guard turns down.
        diagnostics_prefix: where `explain` and `check` live when a guard
            is given.

    Returns the :class:`~dynamic_config_web.Wiring`, so a caller can stop
    it by hand, ask whether it is watching, or reach the configurations it
    covers.
    """
    wiring = (
        target
        if isinstance(target, Wiring)
        else Wiring(target, watch=watch, debounce=debounce, poll_interval=poll_interval)
    )

    previous = app.router.lifespan_context

    @asynccontextmanager
    async def combined(application: FastAPI) -> AsyncIterator[Any]:
        """The wiring's lifespan, around whatever the app already had."""
        await wiring.start_async()

        try:
            async with previous(application) as state:
                yield state
        finally:
            wiring.stop()

    app.router.lifespan_context = combined

    if request_scope:
        # Before the app starts, which is where Starlette requires
        # middleware to be added — and where `setup` is called anyway.
        app.add_middleware(_ScopeMiddleware, wiring=wiring)

    if routes:
        app.include_router(
            router(
                wiring,
                prefix=prefix,
                metrics=metrics,
                stale_after=stale_after,
                guard=guard,
                diagnostics_prefix=diagnostics_prefix,
            )
        )

    return wiring
