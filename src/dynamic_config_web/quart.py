"""Quart: the Flask extension, with a lifespan it can actually use.

    from quart import Quart
    from dynamic_config import DynamicConfig
    from dynamic_config_web.quart import DynamicConfigExtension, snapshot

    config = DynamicConfig(Database, key="db").file("config.toml")
    app = Quart(__name__)

    DynamicConfigExtension(app, config)


    @app.get("/")
    async def index():
        db = snapshot()
        return {"host": db.host}

The API is Flask's, and so is this adapter — with one difference that
matters. Quart is ASGI, so it has `while_serving`: a place that runs after
the workers exist and before the first request, and again on the way down.
That is where the watcher belongs, and it is the thing Flask cannot offer.

Everything else is the same object in the same place: an extension in
`app.extensions`, a blueprint of health routes, `before_request` opening the
request scope, and nothing written into `app.config` — a copy never reloads.
"""

from __future__ import annotations

import threading
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any, Optional

from ._diagnostics import Guard
from ._errors import MissingFrameworkError
from ._routes import RouteContext, RouteError, allowed, route_table
from ._scope import current, enter, get, leave
from ._wiring import Wiring

try:
    from quart import Blueprint, Response, current_app, g, jsonify, request
except ImportError as absent:  # pragma: no cover - exercised in a subprocess
    raise MissingFrameworkError("Quart", "quart") from absent

if TYPE_CHECKING:  # pragma: no cover - typing only
    from quart import Quart

    from dynamic_config import ConfigGroup, DynamicConfig

__all__ = [
    "DynamicConfigExtension",
    "blueprint",
    "extension",
    "snapshot",
    "wiring_of",
]

#: Where the extension lives on the application. The same key Flask's
#: adapter uses, so a blueprint written for one finds the other.
EXTENSION_KEY = "dynamic_config"


def extension(app: Optional[Quart] = None) -> DynamicConfigExtension:
    """The extension installed on `app`, or on the current application."""
    application = app or current_app
    found = application.extensions.get(EXTENSION_KEY)

    if found is None:
        raise RuntimeError(
            "no dynamic-config extension on this application; build one "
            "with `DynamicConfigExtension(app, config)`"
        )

    return found  # type: ignore[no-any-return]


def wiring_of(app: Optional[Quart] = None) -> Wiring:
    """The wiring behind the extension on `app`."""
    return extension(app).wiring


def snapshot(key: Optional[str] = None) -> Any:
    """The model this request began with."""
    if key is not None:
        return get(key)

    return current(extension().wiring.config())


class DynamicConfigExtension:
    """The extension: a lifespan, a request scope, and the health blueprint."""

    def __init__(
        self,
        app: Optional[Quart] = None,
        target: DynamicConfig[Any] | ConfigGroup | Wiring | None = None,
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
        start: str = "serving",
    ) -> None:
        """Builds the extension, and installs it if given an app.

        Parameters:
            start: when the watcher arms. `"serving"` — the default — uses
                `while_serving`, which is the right answer and the reason
                this adapter is not simply Flask's. `"first-request"` is
                for a Quart application mounted inside something that
                never runs a lifespan, and `"manual"` leaves it to you.

        The rest are the arguments every adapter here takes.
        """
        if target is None:
            raise TypeError("the extension needs a configuration to wire")

        if start not in {"serving", "first-request", "manual"}:
            raise ValueError(
                f"{start!r} is not a start: use 'serving', 'first-request' or 'manual'"
            )

        self.wiring = (
            target
            if isinstance(target, Wiring)
            else Wiring(
                target, watch=watch, debounce=debounce, poll_interval=poll_interval
            )
        )
        self._request_scope = request_scope
        self._routes = routes
        self._prefix = prefix
        self._metrics = metrics
        self._stale_after = stale_after
        self._guard = guard
        self._diagnostics_prefix = diagnostics_prefix
        self._start = start
        self._lock = threading.Lock()

        if app is not None:
            self.init_app(app)

    def init_app(self, app: Quart) -> None:
        """Installs the extension on `app`."""
        app.extensions[EXTENSION_KEY] = self

        if self._start == "serving":
            # Quart types the decorator against a callable returning an
            # async generator *function*; an async generator method is
            # what it actually takes, and what its own documentation
            # shows.
            app.while_serving(self._serving)  # type: ignore[type-var]

        if self._request_scope:
            app.before_request(self._open_scope)
            app.teardown_appcontext(self._close_scope)

        if self._start == "first-request" and not self._request_scope:
            app.before_request(self._arm_once)

        if self._routes:
            app.register_blueprint(
                blueprint(
                    self.wiring,
                    prefix=self._prefix,
                    metrics=self._metrics,
                    stale_after=self._stale_after,
                    guard=self._guard,
                    diagnostics_prefix=self._diagnostics_prefix,
                )
            )

    async def _serving(self) -> AsyncIterator[None]:
        """`while_serving`: load and watch, then stop on the way down."""
        await self.wiring.start_async()

        try:
            yield
        finally:
            self.wiring.stop()

    async def start(self) -> None:
        """Loads and starts watching. Idempotent."""
        await self.wiring.start_async()

    def stop(self) -> None:
        """Drops the watcher. Idempotent, and needs no await."""
        self.wiring.stop()

    async def _arm_once(self) -> None:
        """Arms on the first request, for an app with no lifespan."""
        if self._start != "first-request" or self.wiring.started:
            return

        await self.wiring.start_async()

    async def _open_scope(self) -> None:
        """Opens the request scope."""
        if self._start == "first-request":
            await self._arm_once()

        g._dynamic_config_token = enter(self.wiring.configs)

    async def _close_scope(self, _exception: BaseException | None = None) -> None:
        """Closes it, whatever the view did."""
        token = g.pop("_dynamic_config_token", None)

        if token is not None:
            leave(token)

    def __repr__(self) -> str:
        """What it wires, and whether it is running."""
        return f"<DynamicConfigExtension {self.wiring!r}>"


def blueprint(
    wiring: Wiring,
    *,
    prefix: str = "",
    metrics: bool = True,
    stale_after: Optional[float] = None,
    guard: Optional[Guard] = None,
    diagnostics_prefix: str = "/_config",
) -> Blueprint:
    """The health, metrics and diagnostics routes, as a Quart blueprint.

    The shared table, translated: async handlers (Quart's whole point),
    `<path:path>` for the tail, and the WSGI-family refusal convention —
    **404**, a guarded route indistinguishable from an absent one.
    """
    routes = Blueprint("dynamic_config", __name__, url_prefix=prefix or None)

    table = route_table(
        wiring,
        metrics=metrics,
        stale_after=stale_after,
        guard=guard,
        diagnostics_prefix=diagnostics_prefix,
    )

    def make(entry: Any) -> Any:
        async def handler(path: str = "") -> Response:
            if entry.guarded:
                try:
                    allowed(request, guard, refused=404)
                except RouteError as stopped:
                    return _reply_of(stopped)

            context = RouteContext(path_param=path or None, query=request.args)

            try:
                reply = await entry.handle_async(context)
            except RouteError as stopped:
                return _reply_of(stopped)

            return Response(
                reply.body, status=reply.status, mimetype=reply.content_type
            )

        handler.__name__ = f"config_{entry.name}"

        return handler

    for entry in table:
        rule = entry.path.replace("{path}", "<path:path>")
        routes.get(rule)(make(entry))

    return routes


def _reply_of(stopped: RouteError) -> Response:
    """A refusal, in Quart's shape."""
    return _json({"detail": stopped.detail}, stopped.status)


def _json(body: Any, status: int) -> Response:
    """A JSON response with a status.

    `jsonify` is synchronous in every Quart this adapter supports — it
    returns a `Response`, not a coroutine — so this one is too.
    """
    answer = jsonify(body)
    answer.status_code = status

    return answer
