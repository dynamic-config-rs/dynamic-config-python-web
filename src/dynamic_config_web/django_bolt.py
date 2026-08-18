"""django-bolt: a lifespan, a middleware, and a router — at construction.

**Experimental.** django-bolt is 0.10.x, needs Python 3.12, and serves from
Rust; its API is the youngest of the seven and the likeliest to move. What
is here is deliberately small — the three seams it documents, and nothing
private.

    from django_bolt import BoltAPI
    from dynamic_config import DynamicConfig
    from dynamic_config_web.django_bolt import api, snapshot

    config = DynamicConfig(Database, key="db").file("config.toml")
    bolt = api(config)              # a BoltAPI, wired


    @bolt.get("/")
    async def index():
        db = snapshot()
        return {"host": db.host}

`api()` is a factory rather than the `setup(app, config)` the other
adapters offer, and that is django-bolt's doing: its lifespan and its
middleware are **constructor arguments**, so a wiring added after the
`BoltAPI` exists would have to reach past the API into private attributes.
A factory says the same thing without doing that, and it passes every other
keyword straight through::

    bolt = api(config, prefix="/v1", django_middleware=True)

For an application that must build its own `BoltAPI`, the three pieces are
public — `lifespan()`, `ScopeMiddleware` and `router()` — and composing them
by hand is exactly what `api()` does.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, Callable, Optional

from ._diagnostics import Guard
from ._errors import MissingFrameworkError
from ._routes import RouteContext, RouteError, allowed, route_table
from ._scope import current, enter, get, leave
from ._wiring import Wiring

try:
    from django_bolt import BaseMiddleware, BoltAPI, Response, Router
except ImportError as absent:  # pragma: no cover - exercised in a subprocess
    raise MissingFrameworkError("django-bolt", "django-bolt") from absent

if TYPE_CHECKING:  # pragma: no cover - typing only
    from dynamic_config import ConfigGroup, DynamicConfig

__all__ = [
    "ScopeMiddleware",
    "api",
    "lifespan",
    "router",
    "snapshot",
    "wiring",
]

#: What the middleware and `snapshot()` read. As in the Robyn adapter, a
#: django-bolt handler is given its parameters and nothing else, so the
#: wiring has to be reachable without an application in hand.
_ACTIVE: Optional[Wiring] = None


def wiring() -> Wiring:
    """The wiring this process serves."""
    if _ACTIVE is None:
        raise RuntimeError(
            "dynamic-config is not wired into this process; build the API "
            "with dynamic_config_web.django_bolt.api(config)."
        )

    return _ACTIVE


def snapshot(key: Optional[str] = None) -> Any:
    """The model this request began with."""
    if key is not None:
        return get(key)

    return current(wiring().config())


def lifespan(target: DynamicConfig[Any] | ConfigGroup | Wiring) -> Callable[..., Any]:
    """A lifespan for `BoltAPI(lifespan=…)` that loads and watches.

    Registers the wiring as this process's, so the middleware and
    `snapshot()` find it — which happens when the factory is called, not
    when the lifespan runs, because a route may be declared first.
    """
    global _ACTIVE

    running = target if isinstance(target, Wiring) else Wiring(target)
    _ACTIVE = running

    @asynccontextmanager
    async def context(bolt: BoltAPI) -> AsyncIterator[None]:
        """Loaded and watching for the length of the application."""
        del bolt

        await running.start_async()

        try:
            yield
        finally:
            running.stop()

    return context


class ScopeMiddleware(BaseMiddleware):  # type: ignore[misc]
    """Opens one request scope per request.

    django-bolt's middleware is Django's shape — `get_response` in, a
    response out — and it awaits the rest of the chain inside its own
    coroutine, which is what makes a `contextvars` token set here visible
    to the handler. (Robyn's does not, which is why that adapter has a
    decorator where this one has a middleware.)
    """

    async def process_request(self, request: Any) -> Any:
        """Enter, delegate, leave — whatever the handler does."""
        token = enter(wiring().configs)

        try:
            return await self.get_response(request)
        finally:
            leave(token)


def router(
    running: Wiring,
    *,
    prefix: str = "",
    metrics: bool = True,
    stale_after: Optional[float] = None,
    guard: Optional[Guard] = None,
    diagnostics_prefix: str = "/_config",
) -> Router:
    """The health, metrics and diagnostics routes, as a `Router`.

    Every handler here is annotated `-> Any`, and deliberately:
    django-bolt validates a response against its annotation, and
    `-> Response` makes it check the *body* against the `Response`
    class — which a dict body then fails. `Any` is the annotation that
    says "this handler builds its own response", and it is what these do.
    """
    routes: Any = Router(prefix=prefix)

    table = {
        entry.name: entry
        for entry in route_table(
            running,
            metrics=metrics,
            stale_after=stale_after,
            guard=guard,
            diagnostics_prefix=diagnostics_prefix,
        )
    }

    # Declared one by one rather than in a loop: django-bolt validates a
    # handler against its *signature*, and the path parameter's presence
    # changes it. The bodies are the shared table's; only the signatures
    # are this framework's.

    async def answer(entry: Any, request: Any, path: Optional[str]) -> Any:
        if entry.guarded:
            try:
                allowed(request, guard, refused=404)
            except RouteError as stopped:
                return Response({"detail": stopped.detail}, status_code=stopped.status)

        context = RouteContext(
            path_param=None if path is None else path.lstrip("/"),
            query={} if request is None else request.query,
        )

        try:
            reply = await entry.handle_async(context)
        except RouteError as stopped:
            return Response({"detail": stopped.detail}, status_code=stopped.status)

        # Bolt's `Response` serialises its body itself, so a JSON reply
        # goes back to a dict here — handing it the rendered string would
        # double-encode it.
        body: Any = reply.body

        if reply.content_type == "application/json":
            body = json.loads(reply.body)

        return Response(body, status_code=reply.status, media_type=reply.content_type)

    @routes.get("/healthz")
    async def healthz() -> Any:
        return await answer(table["healthz"], None, None)

    @routes.get("/readyz")
    async def readyz() -> Any:
        return await answer(table["readyz"], None, None)

    if "metrics" in table:

        @routes.get("/metrics")
        async def prometheus() -> Any:
            return await answer(table["metrics"], None, None)

    if "explain" in table:

        @routes.get(f"{diagnostics_prefix}/explain/{{path:path}}")
        async def explain_path(request: Any, path: str) -> Any:
            return await answer(table["explain"], request, path)

        @routes.get(f"{diagnostics_prefix}/check")
        async def check_all(request: Any) -> Any:
            return await answer(table["check"], request, None)

    return routes


def api(
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
    middleware: Optional[list[Any]] = None,
    **bolt: Any,
) -> BoltAPI:
    """A `BoltAPI` with the lifespan, the scope and the routes already on it.

    Every other keyword — `prefix` excepted, which this one uses for the
    health routes — goes to `BoltAPI` untouched, so a project loses no
    django-bolt options by taking the factory.
    """
    running = (
        target
        if isinstance(target, Wiring)
        else Wiring(target, watch=watch, debounce=debounce, poll_interval=poll_interval)
    )

    stack = list(middleware or [])

    if request_scope:
        stack.append(ScopeMiddleware)

    built = BoltAPI(lifespan=lifespan(running), middleware=stack, **bolt)

    if routes:
        built.include_router(
            router(
                running,
                prefix=prefix,
                metrics=metrics,
                stale_after=stale_after,
                guard=guard,
                diagnostics_prefix=diagnostics_prefix,
            )
        )

    return built
