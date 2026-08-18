"""Robyn: startup and shutdown handlers, and a scope you ask for.

**Experimental.** Robyn's server is Rust, its workers are processes it
starts itself, and its middleware pipeline is not a place a `contextvars`
token survives — see below. The adapter works and is tested against the
same twelve cases as the rest, and it is the one whose shape may still
change.

    from robyn import Robyn
    from dynamic_config import DynamicConfig
    from dynamic_config_web.robyn import setup, scoped, snapshot

    config = DynamicConfig(Database, key="db").file("config.toml")
    app = Robyn(__file__)

    setup(app, config)


    @app.get("/")
    @scoped
    async def index(request):
        db = snapshot()
        return {"host": db.host}

**`@scoped` is not optional, and it is not decoration.** Every other
adapter here opens the request scope in middleware, because in every other
framework the middleware and the handler share a context. Robyn calls its
before-request middleware and then calls the handler — a `ContextVar` set
in the first is not visible in the second, so a scope opened there would
silently not be there. Rather than pretend, this adapter puts the scope
where it demonstrably holds: around the handler itself.

The consequence is worth stating plainly: a handler without `@scoped` that
calls `snapshot()` raises `OutsideRequestScopeError` rather than quietly
reading a different generation halfway through. That is the trade this
package makes everywhere — a loud absence over a quiet inconsistency.

Order matters: `@scoped` goes **under** `@app.get(...)`, so that what Robyn
registers is the wrapped function.
"""

from __future__ import annotations

import functools
import inspect
import json
import threading
from typing import TYPE_CHECKING, Any, Callable, Optional, TypeVar

from ._diagnostics import Guard
from ._errors import MissingFrameworkError
from ._routes import RouteContext, RouteError, allowed, route_table
from ._scope import current, enter, get, leave
from ._wiring import Wiring

try:
    # `HttpMethod` is public and re-exported, but not in Robyn's
    # `__all__`, which is what the ignore is about.
    from robyn import HttpMethod, Response, SubRouter  # type: ignore[attr-defined]
    from robyn.robyn import Headers
except ImportError as absent:  # pragma: no cover - exercised in a subprocess
    raise MissingFrameworkError("Robyn", "robyn") from absent

if TYPE_CHECKING:  # pragma: no cover - typing only
    from robyn import Robyn

    from dynamic_config import ConfigGroup, DynamicConfig

__all__ = ["router", "scoped", "setup", "snapshot", "wiring"]

Handler = TypeVar("Handler", bound=Callable[..., Any])

#: What `@scoped` and `snapshot()` read. Robyn hands a handler a request
#: and nothing else — no application, no state object — so the wiring has
#: to be reachable without one. Process-wide is the honest scope for that,
#: and it is what a Robyn process is: one `Robyn(__file__)`, one `start()`.
_ACTIVE: Optional[Wiring] = None
_LOCK = threading.Lock()


def wiring() -> Wiring:
    """The wiring this process serves."""
    if _ACTIVE is None:
        raise RuntimeError(
            "dynamic-config is not set up in this process; call "
            "dynamic_config_web.robyn.setup(app, config) before serving."
        )

    return _ACTIVE


def snapshot(key: Optional[str] = None) -> Any:
    """The model this request began with.

    Only inside a `@scoped` handler; anywhere else it raises, which is the
    reminder that the decorator is what makes the read consistent.
    """
    if key is not None:
        return get(key)

    return current(wiring().config())


def scoped(handler: Handler) -> Handler:
    """Opens the request scope around one handler.

        @app.get("/")
        @scoped
        async def index(request):
            ...

    Sync and async handlers both, because Robyn takes both and the scope
    has to be opened in whichever one Robyn will actually call.
    """
    if inspect.iscoroutinefunction(handler):

        @functools.wraps(handler)
        async def asynchronous(*args: Any, **options: Any) -> Any:
            token = enter(wiring().configs)

            try:
                return await handler(*args, **options)
            finally:
                leave(token)

        return asynchronous  # type: ignore[return-value]

    @functools.wraps(handler)
    def synchronous(*args: Any, **options: Any) -> Any:
        token = enter(wiring().configs)

        try:
            return handler(*args, **options)
        finally:
            leave(token)

    return synchronous  # type: ignore[return-value]


def setup(
    app: Robyn,
    target: DynamicConfig[Any] | ConfigGroup | Wiring,
    *,
    watch: bool = True,
    debounce: float = 0.25,
    poll_interval: Optional[float] = None,
    routes: bool = True,
    prefix: str = "",
    metrics: bool = True,
    stale_after: Optional[float] = None,
    guard: Optional[Guard] = None,
    diagnostics_prefix: str = "/_config",
) -> Wiring:
    """Wires `app`: startup, shutdown, and the health routes.

    Answers the :class:`~dynamic_config_web.Wiring`, which is what a test
    holds on to and what a second application shares.

    A word on Robyn's processes. `app.start(processes=N)` starts N *worker
    processes*, each of which runs the startup handler, so each ends up
    watching its own files — which is what you want, and what the lease
    makes safe. Under `--processes` with a pre-forking start the wiring
    re-arms in the child; see :class:`~dynamic_config_web.Wiring`.
    """
    global _ACTIVE

    running = (
        target
        if isinstance(target, Wiring)
        else Wiring(target, watch=watch, debounce=debounce, poll_interval=poll_interval)
    )

    with _LOCK:
        _ACTIVE = running

    async def start() -> None:
        """Robyn's startup event: load and watch, in each worker."""
        await running.start_async()

    def stop() -> None:
        """And its shutdown event."""
        running.stop()

    app.startup_handler(start)
    app.shutdown_handler(stop)

    if routes:
        app.include_router(
            router(
                running,
                prefix=prefix,
                metrics=metrics,
                stale_after=stale_after,
                guard=guard,
                diagnostics_prefix=diagnostics_prefix,
            )
        )

    return running


def router(
    running: Wiring,
    *,
    prefix: str = "",
    metrics: bool = True,
    stale_after: Optional[float] = None,
    guard: Optional[Guard] = None,
    diagnostics_prefix: str = "/_config",
) -> SubRouter:
    """The health, metrics and diagnostics routes, as a `SubRouter`."""
    # `SubRouter(prefix=…)` and not `SubRouter(__file__, …)`: the file
    # argument is deprecated and ignored, and an adapter must not be the
    # reason a user's own suite prints a deprecation warning.
    routes = SubRouter(prefix=prefix)

    table = route_table(
        running,
        metrics=metrics,
        stale_after=stale_after,
        guard=guard,
        diagnostics_prefix=diagnostics_prefix,
    )

    def make(entry: Any) -> Any:
        async def handler(request: Any) -> Response:
            if entry.guarded:
                try:
                    allowed(request, guard, refused=404)
                except RouteError as stopped:
                    return _json({"detail": stopped.detail}, stopped.status)

            query = dict((request.query_params.to_dict() or {}).items())
            # Robyn's query values arrive as lists.
            flat = {
                key: value[0] if isinstance(value, list) else value
                for key, value in query.items()
            }

            context = RouteContext(
                path_param=request.path_params.get("path", "") or None,
                query=flat,
            )

            try:
                reply = await entry.handle_async(context)
            except RouteError as stopped:
                return _json({"detail": stopped.detail}, stopped.status)

            return Response(
                status_code=reply.status,
                headers=Headers({"content-type": reply.content_type}),
                description=reply.body,
            )

        handler.__name__ = f"config_{entry.name}"

        return handler

    for entry in table:
        # `*path` rather than `:path`: a dotted path is one segment, but a
        # caller who writes `database.pool.size` should not have to know
        # that, and the catch-all is what makes a slash in it harmless.
        rule = entry.path.replace("{path}", "*path")
        routes.add_route(HttpMethod.GET, rule, make(entry))

    return routes


def _json(body: Any, status: int) -> Response:
    """A JSON response with a status."""
    return Response(
        status_code=status,
        headers=Headers({"content-type": "application/json"}),
        description=json.dumps(body),
    )


def _text(body: str, media_type: str) -> Response:
    """A text response, with the media type its reader expects."""
    return Response(
        status_code=200,
        headers=Headers({"content-type": media_type}),
        description=body,
    )
