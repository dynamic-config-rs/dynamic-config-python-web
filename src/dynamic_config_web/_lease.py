"""One watcher per configuration, per process, however many ask for it.

The engine refuses a second watcher on one configuration — `AlreadyExists`,
deliberately, because two watchers on one file can only mislead. Every
integration in this package therefore has to answer a question the engine
does not: *is one already running, and is it mine to stop?*

A lease answers it by counting holders rather than by asking. Two
applications over one configuration — a main app and an admin app, a test
that builds a client while another is open, a `ConfigGroup` two wirings
share a member of — each take a lease; the first one starts the watcher
and the last one to leave stops it.

The other half is `fork()`. A watcher is a thread, and a thread does not
survive one; the *registration* does, because it is memory. A child
inheriting both would be refused a fresh watcher while nothing watched —
serving the snapshot it was forked with, for ever, silently. Every lease
re-arms itself in the child instead.
"""

from __future__ import annotations

import contextlib
import os
import threading
import weakref
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:  # pragma: no cover - typing only
    from dynamic_config import DynamicConfig, Watch

__all__ = ["acquire", "acquire_async", "holders", "release"]


@dataclass
class _Lease:
    """What one configuration's watcher is, and who is holding it."""

    watch: Optional[Watch]
    holders: int
    pid: int
    debounce: float
    poll_interval: Optional[float]


#: By configuration, weakly: a configuration nobody holds any more should
#: be collectable, and its lease with it.
_LEASES: weakref.WeakKeyDictionary[Any, _Lease] = weakref.WeakKeyDictionary()

#: Reentrant because the fork handler walks the same table it locks, and
#: because `release` may run from a `__del__` on the thread that holds it.
_LOCK = threading.RLock()


def acquire(
    config: DynamicConfig[Any],
    *,
    watch: bool = True,
    debounce: float = 0.25,
    poll_interval: Optional[float] = None,
) -> None:
    """Takes a hold on `config`'s watcher, starting it if nobody had one.

    `watch=False` still takes the hold — that is what makes a wiring that
    only loads pair with `release` the same way, and what stops a second
    wiring that *does* want a watcher from being surprised by the first.
    """
    with _LOCK:
        lease = _LEASES.get(config)

        if lease is None or lease.pid != os.getpid():
            # No lease, or one inherited from a parent process: either way
            # this process is starting from nothing.
            lease = _Lease(
                watch=None,
                holders=0,
                pid=os.getpid(),
                debounce=debounce,
                poll_interval=poll_interval,
            )
            _LEASES[config] = lease

        lease.holders += 1

        if watch and lease.watch is None:
            lease.watch = config.watch(debounce, poll_interval)
            lease.debounce = debounce
            lease.poll_interval = poll_interval


async def acquire_async(
    config: DynamicConfig[Any],
    *,
    watch: bool = True,
    debounce: float = 0.25,
    poll_interval: Optional[float] = None,
) -> None:
    """:func:`acquire`, with the registration off the event loop.

    Starting a watcher is short and is not free: it resolves the
    directories to observe, registers each with the platform's
    notification backend and spawns the carrier thread — syscalls the
    calling thread waits out, and `poll_interval` first scans everything
    it watches. The loop that starts a service is the loop that will
    answer its requests.
    """
    # The lock is held only around the bookkeeping, never across the await
    # — a lock held across a suspension point is how one slow start blocks
    # every other configuration's.
    with _LOCK:
        lease = _LEASES.get(config)

        if lease is None or lease.pid != os.getpid():
            lease = _Lease(
                watch=None,
                holders=0,
                pid=os.getpid(),
                debounce=debounce,
                poll_interval=poll_interval,
            )
            _LEASES[config] = lease

        lease.holders += 1
        starting = watch and lease.watch is None

    if not starting:
        return

    started = await config.watch_async(debounce, poll_interval)

    with _LOCK:
        lease = _LEASES[config]

        if lease.watch is None:
            lease.watch = started
            lease.debounce = debounce
            lease.poll_interval = poll_interval
        else:
            # Another holder won the race between the two blocks. Theirs is
            # the one everybody else will find, so this one is stopped
            # rather than left running unreferenced.
            started.stop()


def release(config: DynamicConfig[Any]) -> None:
    """Drops a hold, stopping the watcher when the last one goes.

    Idempotent past zero: stopping a wiring twice, or stopping one that
    never started, is not an error — a teardown path is the wrong place
    to be strict.
    """
    with _LOCK:
        lease = _LEASES.get(config)

        if lease is None:
            return

        if lease.pid != os.getpid():
            # Inherited across a fork and never re-armed here: nothing in
            # this process holds it.
            del _LEASES[config]

            return

        lease.holders = max(0, lease.holders - 1)

        if lease.holders > 0:
            return

        watch, lease.watch = lease.watch, None

        del _LEASES[config]

    if watch is not None:
        # Outside the lock: `stop()` drops the notification backend, and a
        # lock held across a call into the engine is a lock held while
        # another thread may be in a hook.
        watch.stop()


def holders(config: DynamicConfig[Any]) -> int:
    """How many wirings are holding this configuration's watcher.

    For the tests, and for the one diagnostic question this module can
    answer that nothing else can: *is anything watching this?*
    """
    with _LOCK:
        lease = _LEASES.get(config)

        if lease is None or lease.pid != os.getpid():
            return 0

        return lease.holders


def watching(config: DynamicConfig[Any]) -> bool:
    """Whether a watcher is running for `config` in this process."""
    with _LOCK:
        lease = _LEASES.get(config)

        return (
            lease is not None and lease.pid == os.getpid() and lease.watch is not None
        )


def _rearm_in_child() -> None:
    """After `fork()`: every inherited lease starts a watcher of its own.

    The child has the parent's registration and none of its threads. So
    each lease drops the handle it inherited — which frees the
    registration — and starts again, on this process's own thread.

    Best effort by construction: a child that cannot watch still serves
    the configuration it was forked with, and raising here would take the
    worker down at the moment it was supposed to start answering.
    """
    with _LOCK:
        leases = list(_LEASES.items())

        for config, lease in leases:
            lease.pid = os.getpid()

            if lease.watch is None:
                continue

            with contextlib.suppress(Exception):
                # The handle may already be useless in this process.
                lease.watch.stop()

            lease.watch = None

            try:
                lease.watch = config.watch(lease.debounce, lease.poll_interval)
            except Exception:
                lease.watch = None


# `fork` is what this handles, and Windows has none — so the guard is the
# attribute rather than the platform name.
if hasattr(os, "register_at_fork"):  # pragma: no branch - true on POSIX
    os.register_at_fork(after_in_child=_rearm_in_child)
