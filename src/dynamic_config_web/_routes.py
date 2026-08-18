"""The route table: what the health surface serves, written once.

Every adapter mounts the same five routes — `/healthz`, `/readyz`,
`/metrics`, and behind a guard `/_config/explain/{path}` and
`/_config/check` — and until 0.2 each adapter re-declared all five with
the same bodies. Six adapters loop over this table now — FastAPI,
Litestar, Flask, Quart, Robyn and django-bolt — translating each
:class:`Reply` into their framework's response type, which is the only
part that differs.

The Django family stays off the table, on purpose: its views late-bind
the *installation* per request (Django settings own the wiring there,
and `AppConfig.ready` may re-run in tests), each view is individually
routable public API, and DRF and Ninja wrap them in their frameworks'
own permission machinery. They consume the same `_health`, `_metrics`
and `_diagnostics` functions this table does — the bodies are shared
one level down.

The table knows no framework. A route handler takes a
:class:`RouteContext` (the two request-shaped things any route here
needs: the path tail and the query string) and answers a :class:`Reply`
(status, rendered body, content type). Refusals are :class:`RouteError`,
which each adapter maps to its framework's own error idiom — that is
where DRF's 403, Ninja's 401 and the plain views' 404 stay theirs.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Optional

from ._diagnostics import Guard, check, check_async, explain, explain_async, never
from ._health import liveness, readiness
from ._metrics import CONTENT_TYPE, metrics_body

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ._wiring import Wiring

__all__ = [
    "Reply",
    "Route",
    "RouteContext",
    "RouteError",
    "allowed",
    "named",
    "route_table",
]

JSON = "application/json"
TEXT = "text/plain; charset=utf-8"


class RouteError(Exception):
    """A refusal, framework-neutrally: the status and a safe detail.

    Adapters translate this into their framework's error response. The
    detail never carries a configuration value — the same rule every
    diagnostic in this package follows.
    """

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


@dataclass(frozen=True)
class Reply:
    """A rendered response: what is left after the framework is gone."""

    status: int
    body: str
    content_type: str


@dataclass(frozen=True)
class RouteContext:
    """The two request-shaped inputs any route in the table needs."""

    path_param: Optional[str] = None
    query: Mapping[str, str] = None  # type: ignore[assignment]

    def query_get(self, key: str) -> Optional[str]:
        return None if self.query is None else self.query.get(key)


@dataclass(frozen=True)
class Route:
    """One mounted path: where, whether it is guarded, and what it does."""

    path: str
    name: str
    guarded: bool
    handle: Callable[[RouteContext], Reply]
    handle_async: Callable[[RouteContext], Awaitable[Reply]]


def allowed(request: Any, guard: Optional[Guard], *, refused: int = 403) -> None:
    """Refuses a request the guard does not accept.

    The guard receives the *framework's* request object — that is the
    guard protocol, and `_header` inside `token_guard` already speaks
    every framework's header idiom. What is shared is only the refusal;
    its *status* stays each adapter's documented convention (`refused`):
    the WSGI-shaped adapters answer 404 so a guarded route is
    indistinguishable from an absent one, DRF answers its own 403, and
    Django Ninja's `auth=` answers 401 before any of this runs.
    """
    if guard is not None and not guard(request):
        detail = "not found" if refused == 404 else "not permitted"

        raise RouteError(refused, detail)


def named(wiring: Wiring, key: Optional[str]) -> Any:
    """The configuration a diagnostics request names, or the only one."""
    configs = wiring.configs

    if key is None:
        if len(configs) == 1:
            return configs[0]

        raise RouteError(
            400,
            "this application has more than one configuration; name one "
            f"with ?config=, from: {', '.join(c.key for c in configs)}",
        )

    for config in configs:
        if config.key == key:
            return config

    raise RouteError(404, f"no configuration named {key!r}")


def route_table(
    wiring: Wiring,
    *,
    metrics: bool = True,
    stale_after: Optional[float] = None,
    guard: Optional[Guard] = None,
    diagnostics_prefix: str = "/_config",
) -> tuple[Route, ...]:
    """The five routes over `wiring`, as data.

    The two diagnostics routes exist only when a real guard was given:
    `None` and the shipped `never` both mean *do not build these* — the
    first because nobody asked, the second because somebody wrote the
    refusal out.
    """

    def healthz(_: RouteContext) -> Reply:
        report = liveness()

        return Reply(report.status_code, json.dumps(report.body), JSON)

    def readyz(_: RouteContext) -> Reply:
        report = readiness(*wiring.configs, stale_after=stale_after)

        return Reply(report.status_code, json.dumps(report.body), JSON)

    def prometheus(_: RouteContext) -> Reply:
        return Reply(200, metrics_body(*wiring.configs), CONTENT_TYPE)

    def explain_path(context: RouteContext) -> Reply:
        config = named(wiring, context.query_get("config"))

        return Reply(200, explain(config, context.path_param or ""), TEXT)

    async def explain_path_async(context: RouteContext) -> Reply:
        config = named(wiring, context.query_get("config"))

        return Reply(200, await explain_async(config, context.path_param or ""), TEXT)

    def check_all(_: RouteContext) -> Reply:
        report = {config.key: check(config) for config in wiring.configs}

        return Reply(200, json.dumps(report), JSON)

    async def check_all_async(_: RouteContext) -> Reply:
        report = {config.key: await check_async(config) for config in wiring.configs}

        return Reply(200, json.dumps(report), JSON)

    def as_async(
        handler: Callable[[RouteContext], Reply],
    ) -> Callable[[RouteContext], Awaitable[Reply]]:
        async def twin(context: RouteContext) -> Reply:
            return handler(context)

        return twin

    routes = [
        Route("/healthz", "healthz", False, healthz, as_async(healthz)),
        Route("/readyz", "readyz", False, readyz, as_async(readyz)),
    ]

    if metrics:
        routes.append(
            Route("/metrics", "metrics", False, prometheus, as_async(prometheus))
        )

    if guard is not None and guard is not never:
        routes.append(
            Route(
                f"{diagnostics_prefix}/explain/{{path}}",
                "explain",
                True,
                explain_path,
                explain_path_async,
            )
        )
        routes.append(
            Route(
                f"{diagnostics_prefix}/check",
                "check",
                True,
                check_all,
                check_all_async,
            )
        )

    return tuple(routes)
