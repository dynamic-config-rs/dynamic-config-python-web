"""Django: an installed app, a middleware, and a settings entry.

    # settings.py
    INSTALLED_APPS = [..., "dynamic_config_web.django"]
    MIDDLEWARE = [
        "dynamic_config_web.django.middleware.DynamicConfigMiddleware",
        ...,
    ]
    DYNAMIC_CONFIG = {"target": "myproject.config:database"}

    # urls.py
    urlpatterns = [
        path("", include("dynamic_config_web.django.urls")),
        ...,
    ]

    # views.py
    from dynamic_config_web.django import snapshot

    def index(request):
        db = snapshot()                      # or request.dynamic_config["db"]
        return JsonResponse({"host": db.host})

**Do not put configuration in `settings`.** Django's settings object is
loaded once and cached for the life of the process — a value copied into it
at startup is frozen there, which is the bug this package exists to prevent.
`DYNAMIC_CONFIG` holds a *pointer* to the configuration, never a value read
out of it, and `snapshot()` is the read.

Django has no lifespan, so the app's `ready()` is what loads and watches. It
runs once per worker under gunicorn and uvicorn, once in each half of
`runserver`'s autoreloader, and once per management command — three shapes
this module tells apart, because only the first two should be watching
files. See :func:`should_watch`.
"""

from __future__ import annotations

import os
import sys
import threading
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from dynamic_config_web._diagnostics import Guard
from dynamic_config_web._errors import MissingFrameworkError
from dynamic_config_web._scope import current, get, latest
from dynamic_config_web._wiring import Wiring

try:
    from django.core.exceptions import ImproperlyConfigured
except ImportError as absent:  # pragma: no cover - exercised in a subprocess
    raise MissingFrameworkError("Django", "django") from absent

if TYPE_CHECKING:  # pragma: no cover - typing only
    from dynamic_config import ConfigGroup, DynamicConfig

__all__ = [
    "Installation",
    "RequestConfig",
    "configure",
    "configure_from_settings",
    "installation",
    "installed",
    "reset",
    "serving_process",
    "should_watch",
    "snapshot",
    "start",
    "stop",
    "wiring",
]


@dataclass(frozen=True)
class Installation:
    """What one Django process was told to serve.

    Django has no application object to hang this on — `settings` is a
    module and the app registry is a singleton — so the installation is
    process-wide by construction. That is not a compromise: a Django
    process serves one project, and :class:`~dynamic_config_web.Wiring`
    already makes "once per process" safe across forks and autoreloads.
    """

    wiring: Wiring
    guard: Optional[Guard] = None
    stale_after: Optional[float] = None
    metrics: bool = True
    diagnostics_prefix: str = "_config"

    def config(self, key: Optional[str] = None) -> DynamicConfig[Any]:
        """One configuration by key, or the only one there is."""
        return self.wiring.config(key)


_INSTALLED: Optional[Installation] = None
_LOCK = threading.RLock()


def configure(
    target: DynamicConfig[Any] | ConfigGroup | Wiring,
    *,
    watch: bool = True,
    debounce: float = 0.25,
    poll_interval: Optional[float] = None,
    guard: Optional[Guard] = None,
    stale_after: Optional[float] = None,
    metrics: bool = True,
    diagnostics_prefix: str = "_config",
) -> Installation:
    """Registers what this process serves, without loading it yet.

    What the app's `ready()` calls after reading `DYNAMIC_CONFIG`, and what
    a project that would rather wire in Python calls directly — from
    `apps.py`, or from an ASGI module, ahead of `django.setup()`'s effect.

    Answers the :class:`Installation`; :func:`start` is what loads it.
    """
    global _INSTALLED

    with _LOCK:
        _INSTALLED = Installation(
            wiring=(
                target
                if isinstance(target, Wiring)
                else Wiring(
                    target,
                    watch=watch,
                    debounce=debounce,
                    poll_interval=poll_interval,
                )
            ),
            guard=guard,
            stale_after=stale_after,
            metrics=metrics,
            diagnostics_prefix=diagnostics_prefix.strip("/"),
        )

        return _INSTALLED


def configure_from_settings(*, watch: Optional[bool] = None) -> Installation:
    """:func:`configure` from `settings.DYNAMIC_CONFIG`.

        DYNAMIC_CONFIG = "myproject.config:database"

    or, with every option spelled out::

        DYNAMIC_CONFIG = {
            "target": "myproject.config:database",
            "watch": True,
            "debounce": 0.25,
            "poll_interval": None,          # seconds, to poll instead
            "guard": "myproject.config:only_operators",
            "stale_after": 300.0,
            "metrics": True,
            "diagnostics_prefix": "_config",
        }

    `target` and `guard` are dotted paths — `package.module:attribute` or
    `package.module.attribute` — resolved here rather than in `settings`,
    because importing a configuration from `settings.py` runs project code
    while Django is still starting and is a well-known way to produce a
    circular import.

    Parameters:
        watch: overrides the setting, which is what the app's `ready()`
            uses to keep a management command from starting a watcher.
    """
    from django.conf import settings

    spec = getattr(settings, "DYNAMIC_CONFIG", None)

    if spec is None:
        raise ImproperlyConfigured(
            "DYNAMIC_CONFIG is not set. Point it at the configuration this "
            'project serves — DYNAMIC_CONFIG = "myproject.config:database" '
            "— or drop 'dynamic_config_web.django' from INSTALLED_APPS and "
            "call dynamic_config_web.django.configure() yourself."
        )

    options: dict[str, Any] = (
        {"target": spec} if isinstance(spec, (str, bytes)) else dict(spec)
    )

    if "target" not in options:
        raise ImproperlyConfigured(
            "DYNAMIC_CONFIG has no 'target'; it names the configuration to "
            'serve, as a dotted path — {"target": "myproject.config:database"}.'
        )

    target = _resolve(options.pop("target"))
    guard = options.pop("guard", None)

    unknown = set(options) - {
        "debounce",
        "diagnostics_prefix",
        "metrics",
        "poll_interval",
        "stale_after",
        "watch",
    }

    if unknown:
        raise ImproperlyConfigured(
            f"DYNAMIC_CONFIG has settings this version does not know: "
            f"{', '.join(sorted(unknown))}"
        )

    if watch is not None:
        # Narrows, never widens: a project that set `watch: False` means it,
        # and `should_watch()` is only ever the answer to "is this process
        # one that *may*".
        options["watch"] = bool(options.get("watch", True)) and watch

    return configure(
        target,
        guard=_resolve(guard) if guard is not None else None,
        **options,
    )


def _resolve(value: Any) -> Any:
    """A dotted path to the object it names, or the object itself.

    Accepts `package.module:attribute` — the entry-point spelling, and the
    unambiguous one — and `package.module.attribute`, which is what Django
    settings use everywhere else and what a reader will write from habit.
    """
    if not isinstance(value, str):
        return value

    if ":" in value:
        module_name, _, attribute = value.partition(":")
    elif "." in value:
        module_name, _, attribute = value.rpartition(".")
    else:
        raise ImproperlyConfigured(
            f"{value!r} is not a dotted path; write it as 'myproject.config:database'."
        )

    try:
        module = import_module(module_name)
    except ImportError as broken:
        raise ImproperlyConfigured(
            f"DYNAMIC_CONFIG names {value!r}, and {module_name!r} could not "
            f"be imported: {broken}"
        ) from broken

    try:
        return getattr(module, attribute)
    except AttributeError as missing:
        raise ImproperlyConfigured(
            f"DYNAMIC_CONFIG names {value!r}, and {module_name!r} has no "
            f"attribute {attribute!r}"
        ) from missing


def installed() -> bool:
    """Whether anything has been configured in this process yet."""
    return _INSTALLED is not None


def installation() -> Installation:
    """What this process serves.

    Raises `ImproperlyConfigured` naming the two ways to set it, which is
    what a middleware listed before the app was installed deserves to say.
    """
    found = _INSTALLED

    if found is None:
        raise ImproperlyConfigured(
            "dynamic-config is not configured in this process. Add "
            "'dynamic_config_web.django' to INSTALLED_APPS and set "
            "DYNAMIC_CONFIG, or call "
            "dynamic_config_web.django.configure(config) yourself."
        )

    return found


def wiring() -> Wiring:
    """The wiring this process serves."""
    return installation().wiring


def start() -> Wiring:
    """Loads, and starts watching. Idempotent, and safe after a fork.

    What the app's `ready()` calls, and what a gunicorn `post_fork` hook
    calls when it would rather be explicit than rely on the re-arm.
    """
    running = wiring()
    running.start()

    return running


def stop() -> None:
    """Drops the watcher. Idempotent.

    A server process exits rather than shutting configuration down, so this
    is for the two callers that do care: a test, and a management command
    that wants its process to end promptly.
    """
    if _INSTALLED is not None:
        _INSTALLED.wiring.stop()


def reset() -> None:
    """Forgets the installation, watcher and all. For tests."""
    global _INSTALLED

    with _LOCK:
        stop()
        _INSTALLED = None


def snapshot(key: Optional[str] = None) -> Any:
    """The model this request began with.

    The read a view, a form, a serializer or a template tag makes — it
    reaches the request's snapshot through `contextvars`, so it needs no
    `request` argument and works the same in a sync view, an async view and
    a function four frames down.

    Raises :class:`~dynamic_config_web.OutsideRequestScopeError` outside a
    request; :func:`dynamic_config_web.latest` is the unscoped read a
    management command wants.
    """
    if key is not None:
        return get(key)

    return current(installation().config())


class RequestConfig:
    """`request.dynamic_config` — the request's snapshot, as a mapping.

        db = request.dynamic_config["db"]
        db = request.dynamic_config.one()      # when there is only one

    The same values :func:`snapshot` answers, reachable from a `request`
    that has been passed down — which is how Django code is usually
    written, and it keeps a view that never imports this package able to
    read configuration.
    """

    __slots__ = ("_wiring",)

    def __init__(self, source: Wiring) -> None:
        """Built by the middleware, one per request."""
        self._wiring = source

    def __getitem__(self, key: str) -> Any:
        """`request.dynamic_config["db"]` — the model for one key."""
        return get(key)

    def get(self, key: str, default: Any = None) -> Any:
        """The model for `key`, or `default` if this scope has no such key."""
        return get(key, default)

    def one(self) -> Any:
        """The only configuration's model; raises if there is more than one."""
        return current(self._wiring.config())

    def latest(self, key: Optional[str] = None) -> Any:
        """The model installed *now* — deliberately not the request's.

        For the rare view that wants to see a reload that landed during its
        own request; everything else should read the snapshot.
        """
        return latest(self._wiring.config(key))

    def keys(self) -> tuple[str, ...]:
        """Every configuration key this request may read."""
        return tuple(config.key for config in self._wiring.configs)

    def __iter__(self) -> Any:
        """Over the keys, so it reads like the mapping it is."""
        return iter(self.keys())

    def __len__(self) -> int:
        """How many configurations this request may read."""
        return len(self._wiring.configs)

    def __contains__(self, key: object) -> bool:
        """Whether this request carries that key."""
        return key in self.keys()

    def __repr__(self) -> str:
        """The keys, never the values."""
        return f"<RequestConfig {', '.join(self.keys())}>"


def serving_process() -> bool:
    """Whether this process exists to answer requests.

    A server, or `runserver` — as against `migrate`, `shell`,
    `collectstatic` and `configcheck`, which are processes that borrow the
    project for a moment. The distinction decides one thing:
    :class:`~dynamic_config_web.django.apps.DynamicConfigAppConfig` fails
    startup when configuration will not load *in a serving process*, and
    lets a command run anyway — because the command you reach for when
    configuration is broken must not be one that configuration can stop.
    """
    command = _management_command()

    return command is None or command == "runserver"


def should_watch() -> bool:
    """Whether *this* process is one that should be watching files.

    Three shapes arrive at `ready()` and only two of them should watch:

    **A server** — gunicorn, uvicorn, mod_wsgi, granian. Every worker
    watches its own files; a pre-forked worker re-arms after the fork
    because :class:`~dynamic_config_web.Wiring` registers for it.

    **`runserver`** — two processes, and only the child serves. The parent
    is the autoreloader, distinguished by `RUN_MAIN`, and a watcher there
    would hold a notification handle nothing reads. With `--noreload` there
    is one process and it is the server.

    **A management command** — `migrate`, `shell`, `collectstatic`. It
    still loads, because a command reads configuration too, but a watcher
    thread would only delay the exit of a process that is about to end.
    """
    command = _management_command()

    if command is None:
        return True

    if command != "runserver":
        return False

    # `runserver` from here down: two processes, and only one of them.

    if "--noreload" in sys.argv:
        return True

    return os.environ.get("RUN_MAIN") == "true"


def _management_command() -> Optional[str]:
    """The `manage.py` subcommand this process is running, if it is one."""
    if len(sys.argv) < 2:
        return None

    program = Path(sys.argv[0]).name

    # The three ways Django's own documentation starts a command, plus the
    # `python -m django` form.
    if program in {"manage.py", "django-admin", "django-admin.py", "__main__.py"}:
        return sys.argv[1]

    return None
