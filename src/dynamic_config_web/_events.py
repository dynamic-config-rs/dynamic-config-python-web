"""Reloads, in the log a service already has.

A reload that nobody logged is a deployment nobody can correlate with the
graph that moved an hour later. The engine reports every install and every
refusal; this puts them through `logging`, which is the one observability
dependency every web application already has.

**Paths, never values.** `changed_paths` compares two models and answers
which dotted paths moved — comparing secrets without printing them, which
is exactly what a log line needs.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any, Optional

from dynamic_config import Reloaded, ReloadFailed, changed_paths

if TYPE_CHECKING:  # pragma: no cover - typing only
    from dynamic_config import ConfigGroup, DynamicConfig, HookGuard

__all__ = ["log_reloads", "stream_events"]


def log_reloads(
    target: DynamicConfig[Any] | ConfigGroup,
    logger: Optional[logging.Logger] = None,
    *,
    level: int = logging.INFO,
) -> list[HookGuard]:
    """Logs one line per install: which paths moved, and nothing else.

        log_reloads(config, app.logger)

    Registered as an `on_reload` hook per configuration, so it fires for a
    watcher-driven reload exactly as it does for an explicit one. The
    hooks are inline — a log line is the cheap work `Dispatch.INLINE` is
    for — and the guards come back so a caller who cares can close them.

    A refusal fires no hook, because nothing installed; that is what
    :func:`stream_events` is for.
    """
    log = logger or logging.getLogger("dynamic_config")
    guards: list[HookGuard] = []

    for config in _members(target):
        guards.append(config.on_reload(_line(config, log, level)))

    return guards


def _line(config: Any, log: logging.Logger, level: int) -> Any:
    """The hook itself: one line, built from paths."""

    def hook(previous: Any, current: Any) -> None:
        if previous is None:
            log.log(
                level,
                "configuration %s loaded (generation %s)",
                config.key,
                config.generation,
            )

            return

        moved = ", ".join(change.path for change in changed_paths(previous, current))

        log.log(
            level,
            "configuration %s reloaded (generation %s): %s",
            config.key,
            config.generation,
            moved or "nothing moved",
        )

    return hook


async def stream_events(
    config: DynamicConfig[Any],
    *,
    failure_poll: Optional[float] = 1.0,
) -> AsyncIterator[dict[str, Any]]:
    """Installs *and* refusals, as JSON-shaped dictionaries.

        async for event in stream_events(config):
            await websocket.send_json(event)

    What a server-sent-events route, a websocket or a structured logger
    consumes. `failure_poll` is what makes a refusal visible at all: a
    load that installed nothing bumps no generation, so there is nothing
    to wake a stream with — the engine checks the status on that interval
    instead. `None` reports installs only and starts no timer.

    No event carries a value.
    """
    async for event in config.events(failure_poll=failure_poll):
        if isinstance(event, ReloadFailed):
            yield {
                "type": "reload_failed",
                "key": config.key,
                "generation": event.generation,
                "at": event.at,
                "kind": event.kind,
                "path": event.path,
                "consecutive": event.consecutive,
            }
        elif isinstance(event, Reloaded):
            yield {
                "type": "reloaded",
                "key": config.key,
                "generation": event.generation,
                "at": event.at,
                "changed": list(event.changed),
                "reason": event.reason,
            }


def _members(target: Any) -> tuple[Any, ...]:
    """Every configuration in `target`, a group flattened."""
    members = getattr(target, "configs", None)

    return (target,) if members is None else tuple(members)
