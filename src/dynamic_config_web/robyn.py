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

from ._diagnostics import Guard, check_async, explain_async, never
from ._errors import MissingFrameworkError
from ._health import liveness, readiness
from ._metrics import CONTENT_TYPE, metrics_body
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

    async def healthz(request: Any) -> Response:
        """The process is up. Configuration has no say in this one."""
        del request

        report = liveness()

        return _json(report.body, report.status_code)

    async def readyz(request: Any) -> Response:
        """Serving something, and the reloads since have worked."""
        del request

        report = readiness(*running.configs, stale_after=stale_after)

        return _json(report.body, report.status_code)

    routes.add_route(HttpMethod.GET, "/healthz", healthz)
    routes.add_route(HttpMethod.GET, "/readyz", readyz)

    if metrics:

        async def prometheus(request: Any) -> Response:
            """The engine's series, built per scrape."""
            del request

            return _text(metrics_body(*running.configs), CONTENT_TYPE)

        routes.add_route(HttpMethod.GET, "/metrics", prometheus)

    if guard is not None and guard is not never:

        async def explain_path(request: Any) -> Response:
            """Every layer's answer for one dotted path, off the loop."""
            if not guard(request):
                return _json({"detail": "not found"}, 404)

            try:
                config = running.config(
                    request.query_params.get("config", None) or None
                )
            except LookupError as unknown:
                return _json({"detail": str(unknown)}, 400)

            path = request.path_params.get("path", "")

            return _text(await explain_async(config, path), "text/plain")

        async def check_all(request: Any) -> Response:
            """Would each configuration load, and any unknown keys."""
            if not guard(request):
                return _json({"detail": "not found"}, 404)

            return _json(
                {config.key: await check_async(config) for config in running.configs},
                200,
            )

        # `*path` rather than `:path`: a dotted path is one segment, but a
        # caller who writes `database.pool.size` should not have to know
        # that, and the catch-all is what makes a slash in it harmless.
        routes.add_route(
            HttpMethod.GET, f"{diagnostics_prefix}/explain/*path", explain_path
        )
        routes.add_route(HttpMethod.GET, f"{diagnostics_prefix}/check", check_all)

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
