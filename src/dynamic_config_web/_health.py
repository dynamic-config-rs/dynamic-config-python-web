"""Liveness and readiness, which are not the same question.

*Am I serving something* and *did the last reload work* are separate
conditions, and a service that conflates them either refuses traffic it
could serve or accepts traffic on a configuration nobody has been able to
reload for an hour. The Python book states the pair; this builds the two
answers so nine adapters do not each write them.

**No value ever reaches the body.** Generations, counts, kinds, paths and
seconds — the same rule the engine's own diagnostics follow, and for the
same reason: a health endpoint is the most-scraped, least-guarded route a
service has.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from dynamic_config import ConfigGroup, ConfigStatus, DynamicConfig

__all__ = ["HealthReport", "liveness", "readiness"]


@dataclass(frozen=True)
class HealthReport:
    """One answer: a body, and the status code that goes with it."""

    ok: bool
    status_code: int
    body: dict[str, Any]

    def __bool__(self) -> bool:
        """`if report:` — the same question as `report.ok`."""
        return self.ok


def liveness() -> HealthReport:
    """Always 200. The process is running; that is the whole question.

    Configuration has no say in liveness, and that is the point: a
    configuration that will not reload is a reason to stop *routing* to a
    process, never a reason to have it killed and restarted — the restart
    would read the same broken file, fail to start at all, and turn a
    degraded service into an outage. Readiness is where configuration
    speaks.
    """
    return HealthReport(ok=True, status_code=200, body={"status": "ok"})


def readiness(
    *targets: DynamicConfig[Any] | ConfigGroup,
    stale_after: float | None = None,
) -> HealthReport:
    """Whether every configuration is serving, and how the reloads have gone.

    Three conditions, in the order they matter:

    1. **Nothing installed** — the first load has not happened or did not
       succeed. 503, and the only one of the three that means *this
       process has never been able to serve*.
    2. **Not healthy** — something is installed and the reloads since have
       been failing. 503: the process serves, but on a document that is
       older than whoever is editing the file believes.
    3. **Stale** — with `stale_after`, a document nothing has refreshed in
       that many seconds. 503. Off by default, because a configuration
       nobody edits is not a problem and a deployment that only changes
       quarterly should not page anybody.

    Parameters:
        targets: configurations, groups, or a mix. A group's members are
            reported individually, by key.
        stale_after: seconds since the last install, after which a
            configuration counts as stale. `None` — the default — never
            does.
    """
    configs = list(_members(targets))
    entries: dict[str, Any] = {}
    problems: list[str] = []
    #: Something is not installed at all, which is a different sentence
    #: from "installed, and the reloads since have been failing".
    missing = False

    for config in configs:
        status = config.status()
        installed = config.try_current() is not None
        entry = _describe(status, installed=installed)

        if not installed:
            entry["ready"] = False
            missing = True
            problems.append(f"{config.key}: nothing installed")
        elif not status.is_healthy:
            entry["ready"] = False
            problems.append(
                f"{config.key}: {status.consecutive_failures} reloads refused"
            )
        elif stale_after is not None and _is_stale(status, stale_after):
            entry["ready"] = False
            problems.append(f"{config.key}: no install for {status.stale_for:.0f}s")
        else:
            entry["ready"] = True

        entries[config.key] = entry

    ok = not problems
    # Three words rather than two, because an operator reads them: `ok`,
    # `degraded` — serving, but the reloads are failing or the document is
    # old — and `unavailable`, which means this process has never managed
    # to serve that configuration at all.
    body: dict[str, Any] = {
        "status": "ok" if ok else ("unavailable" if missing else "degraded"),
        "configs": entries,
    }

    if problems:
        # The reason, in the body rather than only in the code, because a
        # 503 that says which configuration and why is the difference
        # between a page somebody can act on and one they have to
        # investigate.
        body["problems"] = problems

    return HealthReport(ok=ok, status_code=200 if ok else 503, body=body)


def _members(
    targets: Iterable[DynamicConfig[Any] | ConfigGroup],
) -> Iterable[DynamicConfig[Any]]:
    """Every configuration in `targets`, groups flattened."""
    for target in targets:
        members = getattr(target, "configs", None)

        if members is None:
            yield target  # type: ignore[misc]
        else:
            yield from members


def _is_stale(status: ConfigStatus, stale_after: float) -> bool:
    """Whether the last install is older than `stale_after` seconds."""
    return status.stale_for is not None and status.stale_for > stale_after


def _describe(status: ConfigStatus, *, installed: bool) -> dict[str, Any]:
    """One configuration's status as a body — paths and counts, no values."""
    entry: dict[str, Any] = {
        "installed": installed,
        "generation": status.generation,
        "healthy": status.is_healthy,
        "consecutive_failures": status.consecutive_failures,
    }

    if status.stale_for is not None:
        # Rounded: a health body is read by a human at three in the
        # morning, and six decimal places of seconds help nobody.
        entry["stale_for"] = round(status.stale_for, 1)

    if status.last_reason is not None:
        entry["last_reason"] = str(status.last_reason)

    if status.last_failure is not None:
        # The kind and the path — never the value, and never the message,
        # which routinely carries one.
        entry["last_failure"] = {
            "kind": status.last_failure.kind,
            "path": status.last_failure.path,
            "seconds_ago": round(status.last_failure.seconds_ago, 1),
        }

    return entry
