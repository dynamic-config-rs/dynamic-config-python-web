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

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, Callable, Optional

from ._diagnostics import Guard, check_async, explain_async, never
from ._errors import MissingFrameworkError
from ._health import liveness, readiness
from ._metrics import CONTENT_TYPE, metrics_body
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

    @routes.get("/healthz")
    async def healthz() -> Any:
        """The process is up. Configuration has no say in this one."""
        report = liveness()

        return Response(report.body, status_code=report.status_code)

    @routes.get("/readyz")
    async def readyz() -> Any:
        """Serving something, and the reloads since have worked."""
        report = readiness(*running.configs, stale_after=stale_after)

        return Response(report.body, status_code=report.status_code)

    if metrics:

        @routes.get("/metrics")
        async def prometheus() -> Any:
            """The engine's series, built per scrape."""
            return Response(
                metrics_body(*running.configs),
                media_type=CONTENT_TYPE,
            )

    if guard is not None and guard is not never:

        @routes.get(f"{diagnostics_prefix}/explain/{{path:path}}")
        async def explain_path(request: Any, path: str) -> Any:
            """Every layer's answer for one dotted path, off the loop."""
            if not guard(request):
                return Response({"detail": "not found"}, status_code=404)

            try:
                # `request.query`, which is django-bolt's name for it.
                config = running.config(request.query.get("config"))
            except LookupError as unknown:
                return Response({"detail": str(unknown)}, status_code=400)

            return Response(
                await explain_async(config, path.lstrip("/")),
                media_type="text/plain",
            )

        @routes.get(f"{diagnostics_prefix}/check")
        async def check_all(request: Any) -> Any:
            """Would each configuration load, and any unknown keys."""
            if not guard(request):
                return Response({"detail": "not found"}, status_code=404)

            return Response(
                {config.key: await check_async(config) for config in running.configs}
            )

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
