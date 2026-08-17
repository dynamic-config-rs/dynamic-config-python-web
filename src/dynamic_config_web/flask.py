"""Flask: an extension object, a blueprint, and a watcher that starts late.

    from flask import Flask
    from dynamic_config import DynamicConfig
    from dynamic_config_web.flask import DynamicConfigExtension, snapshot

    config = DynamicConfig(Database, key="db").file("config.toml")
    app = Flask(__name__)

    DynamicConfigExtension(app, config)


    @app.get("/")
    def index():
        db = snapshot()          # the model this request began with
        return {"host": db.host}

**The habit worth breaking is `app.config.update(...)`.** Values copied into
`app.config` at startup are frozen at the moment they were copied, which is
exactly the thing this library exists to fix. This extension deliberately
writes nothing into `app.config`; `snapshot()` is the read.

WSGI has no lifespan, which makes the watcher the interesting part. There is
no moment that is *after the fork* and *before the first request* that a
library can hook — so the watcher arms itself on the first request in each
process, which is after the fork by construction and works the same under
`gunicorn --preload`, uWSGI without `lazy-apps`, and `flask run`.
`start="eager"` is available for a single-process deployment, and
`start="manual"` for a `post_fork` hook that would rather be explicit.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any, Optional

from ._diagnostics import Guard, check, explain, never
from ._errors import MissingFrameworkError
from ._health import liveness, readiness
from ._metrics import CONTENT_TYPE, metrics_body
from ._scope import current, enter, get, leave
from ._wiring import Wiring

try:
    from flask import Blueprint, Response, current_app, g, jsonify, request
except ImportError as absent:  # pragma: no cover - exercised in a subprocess
    raise MissingFrameworkError("Flask", "flask") from absent

if TYPE_CHECKING:  # pragma: no cover - typing only
    from flask import Flask

    from dynamic_config import ConfigGroup, DynamicConfig

__all__ = [
    "DynamicConfigExtension",
    "blueprint",
    "extension",
    "snapshot",
    "wiring_of",
]

#: Where the extension lives on the application, and the name a blueprint
#: reaches for.
EXTENSION_KEY = "dynamic_config"


def extension(app: Optional[Flask] = None) -> DynamicConfigExtension:
    """The extension installed on `app`, or on the current application.

    Raises `RuntimeError` naming the extension when it is not installed,
    which is what a blueprint registered on the wrong app should hear.
    """
    application = app or current_app
    found = application.extensions.get(EXTENSION_KEY)

    if found is None:
        raise RuntimeError(
            "no dynamic-config extension on this application; build one "
            "with `DynamicConfigExtension(app, config)`"
        )

    return found  # type: ignore[no-any-return]


def wiring_of(app: Optional[Flask] = None) -> Wiring:
    """The wiring behind the extension on `app`."""
    return extension(app).wiring


def snapshot(key: Optional[str] = None) -> Any:
    """The model this request began with.

    With no argument, the only configuration the extension was given; with
    one, that section by key. Outside a request it raises, which is the
    point — `dynamic_config_web.latest(config)` is the unscoped read.
    """
    if key is not None:
        return get(key)

    return current(extension().wiring.config())


class DynamicConfigExtension:
    """The extension: a wiring, a request scope, and the health blueprint.

        DynamicConfigExtension(app, config)

    Or the factory shape Flask extensions are expected to support::

        configuration = DynamicConfigExtension(config=config)

        def create_app():
            app = Flask(__name__)
            configuration.init_app(app)

            return app
    """

    def __init__(
        self,
        app: Optional[Flask] = None,
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
        start: str = "first-request",
    ) -> None:
        """Builds the extension, and installs it if given an app.

        Parameters:
            app: the application, or `None` for the factory shape.
            target: a configuration, a `ConfigGroup`, or a `Wiring`.
            start: when the watcher arms.
                `"first-request"` — the default — arms it on the first
                request each process serves, which is after a fork and is
                the only moment WSGI reliably offers.
                `"eager"` arms it in `init_app`, which is right for a
                single process and **wrong** under `--preload`, where the
                master would be the one watching.
                `"manual"` leaves it to you: `extension.start()` from a
                gunicorn `post_fork` hook is the explicit form.
        """
        if target is None:
            raise TypeError("the extension needs a configuration to wire")

        if start not in {"first-request", "eager", "manual"}:
            raise ValueError(
                f"{start!r} is not a start: use 'first-request', 'eager' or 'manual'"
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
        self._armed = False
        self._lock = threading.Lock()

        if app is not None:
            self.init_app(app)

    def init_app(self, app: Flask) -> None:
        """Installs the extension on `app`. The factory shape's half."""
        app.extensions[EXTENSION_KEY] = self

        if self._request_scope:
            app.before_request(self._open_scope)
            app.teardown_appcontext(self._close_scope)
        elif self._start == "first-request":
            # Still needs somewhere to arm from.
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

        if self._start == "eager":
            self.start()

    def start(self) -> None:
        """Loads and starts watching. Idempotent, and safe after a fork.

        What a `post_fork` hook calls, and what the first request calls
        when `start="first-request"`.
        """
        with self._lock:
            self.wiring.start()
            self._armed = True

    def stop(self) -> None:
        """Drops the watcher. Idempotent."""
        with self._lock:
            self.wiring.stop()
            self._armed = False

    # ── the request scope ─────────────────────────────────────────────

    def _arm_once(self) -> None:
        """Arms the wiring on the first request this process serves."""
        if self._start != "first-request":
            return

        # Cheap after the first: one attribute read per request, and the
        # lock is only taken on the way in.
        if self._armed and self.wiring.started:
            return

        self.start()

    def _open_scope(self) -> None:
        """Opens the scope, and arms the watcher if it is the first request."""
        self._arm_once()
        # On `g`, because Flask's app context is what tears it down and the
        # token has to travel with it.
        g._dynamic_config_token = enter(self.wiring.configs)

    def _close_scope(self, _exception: BaseException | None = None) -> None:
        """Closes the scope, whatever the view did."""
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
    """The health, metrics and diagnostics routes, as a blueprint."""
    routes = Blueprint("dynamic_config", __name__, url_prefix=prefix or None)

    @routes.get("/healthz")
    def healthz() -> Response:
        """The process is up. Configuration has no say in this one."""
        report = liveness()

        return _json(report.body, report.status_code)

    @routes.get("/readyz")
    def readyz() -> Response:
        """Serving something, and the reloads since have worked."""
        report = readiness(*wiring.configs, stale_after=stale_after)

        return _json(report.body, report.status_code)

    if metrics:

        @routes.get("/metrics")
        def prometheus() -> Response:
            """The engine's series, built per scrape."""
            return Response(metrics_body(*wiring.configs), mimetype=CONTENT_TYPE)

    if guard is not None and guard is not never:
        diagnostics = Blueprint(
            "dynamic_config_diagnostics", __name__, url_prefix=diagnostics_prefix
        )

        @diagnostics.get("/explain/<path:path>")
        def explain_path(path: str) -> Response:
            """Every layer's answer for one dotted path.

            Synchronous, and that is fine here: WSGI has no event loop to
            keep free, and this is the one place in the package where the
            blocking form is the right one.
            """
            if not guard(request):
                return _json({"detail": "not found"}, 404)

            try:
                config = wiring.config(request.args.get("config"))
            except LookupError as unknown:
                return _json({"detail": str(unknown)}, 400)

            return Response(explain(config, path), mimetype="text/plain")

        @diagnostics.get("/check")
        def check_all() -> Response:
            """Would each configuration load, and any unknown keys."""
            if not guard(request):
                return _json({"detail": "not found"}, 404)

            return _json(
                {config.key: check(config) for config in wiring.configs},
                200,
            )

        routes.register_blueprint(diagnostics)

    return routes


def _json(body: Any, status: int) -> Response:
    """A JSON response with a status, in the one place Flask needs both."""
    answer = jsonify(body)
    answer.status_code = status

    return answer
