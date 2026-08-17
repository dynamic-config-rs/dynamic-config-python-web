"""Loading and watching, once per process, whatever the server does to it.

Every adapter needs the same three moments — load before serving, watch
while serving, stop on the way out — and every framework spells them
differently. This is those three moments, written once.

What it adds to calling `init()` and `watch()` by hand is the two things
that go wrong when a web application does:

**An application object is built more than once.** `uvicorn --reload`
rebuilds it on every edit, a test suite builds one per client, and a
factory-style `create_app()` may be called twice in one process. A second
`watch()` on one configuration is `AlreadyExists`, so "start the watcher
when the app starts" has to mean *start it if nothing is watching yet* —
which is what :mod:`dynamic_config_web._lease` counts.

**A watcher does not survive `fork()`.** Under `gunicorn --preload` the
application is built in the master and the workers are forked from it; the
child inherits the registration and not the thread. The lease re-arms
every watcher in the child, so a pre-forked worker watches its own files
rather than serving one snapshot for ever.
"""

from __future__ import annotations

import os
import threading
from typing import TYPE_CHECKING, Any, Optional

from . import _lease

if TYPE_CHECKING:  # pragma: no cover - typing only
    from dynamic_config import ConfigGroup, DynamicConfig

__all__ = ["Wiring"]


class Wiring:
    """The lifecycle of one configuration, or of a group, for one process.

        wiring = Wiring(config, debounce=0.25)

        wiring.start()          # or `await wiring.start_async()`
        ...
        wiring.stop()

    Idempotent at both ends: starting a wiring that is running does
    nothing, and stopping one that is not is not an error. That is what
    makes it safe to call from a framework hook that fires more than once
    — Django's `AppConfig.ready()` under the autoreloader, a Flask app
    whose first two requests race.

    Usable as a context manager, and as an async one, which is the shape
    that cannot leak a watcher by forgetting to stop it.
    """

    __slots__ = (
        "__weakref__",
        "_debounce",
        "_held",
        "_lock",
        "_pid",
        "_poll",
        "_target",
        "_watch",
    )

    def __init__(
        self,
        target: DynamicConfig[Any] | ConfigGroup,
        *,
        watch: bool = True,
        debounce: float = 0.25,
        poll_interval: Optional[float] = None,
    ) -> None:
        """Builds the wiring; nothing is loaded until it is started.

        Parameters:
            target: one configuration, or a `ConfigGroup` of them.
            watch: whether to start a watcher at all. `False` still
                loads — the right answer for a serverless function, where
                nothing lives long enough to watch, and for a test that
                does its own reloading.
            debounce: seconds to wait after a change before reloading. An
                editor's atomic save is several filesystem events, and
                this is what makes them one reload.
            poll_interval: seconds between polls, which chooses polling
                over the platform's notification backend — what a
                container bind mount and a network share need, where a
                native watch registers successfully and never fires.
        """
        self._target = target
        self._watch = watch
        self._debounce = debounce
        self._poll = poll_interval
        self._lock = threading.RLock()
        #: The configurations this wiring is currently holding a lease on.
        self._held: list[Any] = []
        #: Which process started it. A wiring inherited by a child is not
        #: started there, whatever it remembers.
        self._pid: Optional[int] = None

    # ── lifecycle ─────────────────────────────────────────────────────

    def start(self) -> None:
        """Loads, and starts watching. Idempotent.

        The load is the blocking half — files read, parsed, validated — so
        on an event loop prefer :meth:`start_async`, which is this with the
        wait moved to a worker.
        """
        with self._lock:
            if self.started:
                return

            self._target.init()

            for config in self.configs:
                _lease.acquire(
                    config,
                    watch=self._watch,
                    debounce=self._debounce,
                    poll_interval=self._poll,
                )
                self._held.append(config)

            self._pid = os.getpid()

    async def start_async(self) -> None:
        """:meth:`start`, without blocking the event loop."""
        with self._lock:
            if self.started:
                return

            self._pid = os.getpid()

        await self._target.init_async()

        for config in self.configs:
            await _lease.acquire_async(
                config,
                watch=self._watch,
                debounce=self._debounce,
                poll_interval=self._poll,
            )

            with self._lock:
                self._held.append(config)

    def stop(self) -> None:
        """Drops this wiring's hold on every watcher it took. Idempotent.

        Not a twin of :meth:`start_async`: stopping drops the notification
        backend and returns without joining the watcher thread or waiting
        out a debounce window, so there is nothing to await. A shutdown
        handler may call it directly.
        """
        with self._lock:
            while self._held:
                _lease.release(self._held.pop())

            self._pid = None

    @property
    def started(self) -> bool:
        """Whether this wiring is running **in this process**."""
        return self._pid == os.getpid()

    @property
    def watching(self) -> bool:
        """Whether a watcher is running for every configuration it holds."""
        return bool(self._held) and all(
            _lease.watching(config) for config in self._held
        )

    @property
    def target(self) -> DynamicConfig[Any] | ConfigGroup:
        """What it was built over — the configuration, or the group."""
        return self._target

    @property
    def configs(self) -> tuple[DynamicConfig[Any], ...]:
        """Every configuration behind it, a group flattened.

        What the request scope, the health surface and the metrics body
        all iterate — so a wiring over a group and one over a single
        configuration have nothing else to tell apart.
        """
        members = getattr(self._target, "configs", None)

        if members is None:
            return (self._target,)  # type: ignore[return-value]

        return tuple(members)

    def config(self, key: Optional[str] = None) -> DynamicConfig[Any]:
        """One configuration by key, or the only one there is.

        Raises `LookupError` naming the keys it does have, which is the
        error a typo in a route deserves.
        """
        configs = self.configs

        if key is None:
            if len(configs) == 1:
                return configs[0]

            raise LookupError(
                "this wiring covers more than one configuration; name one "
                f"of {', '.join(repr(config.key) for config in configs)}"
            )

        for config in configs:
            if config.key == key:
                return config

        raise LookupError(
            f"no configuration named {key!r} here; this wiring covers "
            f"{', '.join(repr(config.key) for config in configs)}"
        )

    def __enter__(self) -> Wiring:
        """`with Wiring(config) as wiring:` — started here, stopped after."""
        self.start()

        return self

    def __exit__(self, *_exception: object) -> None:
        self.stop()

    async def __aenter__(self) -> Wiring:
        """The same, awaited, for a service that starts on a loop."""
        await self.start_async()

        return self

    async def __aexit__(self, *_exception: object) -> None:
        self.stop()

    def __repr__(self) -> str:
        """What it wraps and whether it is running — never a value."""
        keys = ", ".join(config.key for config in self.configs)
        state = "started" if self.started else "stopped"

        return f"<Wiring {keys} {state}>"
