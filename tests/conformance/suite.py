"""The contract every adapter passes, written once.

Seven frameworks, one set of promises. What differs between them is the
vocabulary — a `Depends`, an extension object, a plugin, a middleware —
and what must not differ is the behaviour: one reading per request, one
watcher per app lifetime, a readiness endpoint that tells *serving
something* from *the last reload worked*, and a metrics body with no
values in it.

So the assertions live here and each adapter supplies a **driver**: a
small object that knows how to build an application, run its lifetime and
issue a request. An adapter is then thirty lines of test — the driver —
and inherits every case below. A framework that cannot pass one is
documented as not supporting it, in the book, rather than quietly
skipping.
"""

from __future__ import annotations

import time
from contextlib import AbstractContextManager, suppress
from pathlib import Path
from typing import Any, Protocol

from dynamic_config import DynamicConfig
from dynamic_config_web import Wiring
from dynamic_config_web._lease import holders

__all__ = ["Answer", "Driver", "cases"]


class Answer(Protocol):
    """The two things every case asks of a response.

    `status_code` rather than `status`, because that is what httpx,
    Starlette, Flask, Quart and Django's test clients all call it.
    """

    @property
    def status_code(self) -> int: ...

    def json(self) -> Any: ...

    @property
    def text(self) -> str: ...


class Client(Protocol):
    """One request, however the framework's test client spells it."""

    def get(self, path: str, headers: dict[str, str] | None = ...) -> Answer: ...


class Driver(Protocol):
    """What an adapter has to teach this suite.

    Three things: how to build an application over a wiring, how to run
    that application's lifetime, and how to ask it for a path. Everything
    else is shared.
    """

    name: str

    #: Whether the adapter can enforce the request scope for the caller.
    #: `False` for a framework whose middleware and handler are not
    #: guaranteed to share a `contextvars` context — the scope is then
    #: opened by a decorator on the handler, and the "a missing scope
    #: raises" case is skipped rather than pretended.
    scope_is_automatic: bool = True

    def client(
        self,
        wiring: Wiring,
        *,
        guard: Any = None,
        stale_after: float | None = None,
    ) -> AbstractContextManager[Client]:
        """An application over `wiring`, entered for the block."""
        ...


def _settle(check: Any, *, seconds: float = 5.0) -> bool:
    """Waits for something a watcher does, without pinning a duration."""
    deadline = time.monotonic() + seconds

    while time.monotonic() < deadline:
        if check():
            return True

        time.sleep(0.02)

    return False


# ── the cases ──────────────────────────────────────────────────────────
#
# Each is a plain function taking the driver and the fixtures it needs, so
# an adapter's test module is `pytest.mark.parametrize` over `cases()` and
# nothing else.


def case_a_request_reads_the_configuration(
    driver: Driver, wiring: Wiring, config_file: Path
) -> None:
    """The ordinary path: a handler answers with what is installed."""
    with driver.client(wiring) as client:
        answer = client.get("/probe")

        assert answer.status_code == 200
        assert answer.json()["host"] == "db.internal"


def case_a_request_never_tears_across_a_reload(
    driver: Driver, wiring: Wiring, config_file: Path
) -> None:
    """The rule this package exists for.

    The handler reads, a reload lands, the handler reads again — and both
    reads are the same object. Without the scope the second read would
    answer with the new document and one request would have served two
    configurations.
    """
    with driver.client(wiring) as client:
        answer = client.get("/tear")

        assert answer.status_code == 200

        body = answer.json()

        assert body["first"] == body["second"], "one request saw two configurations"
        assert body["same_object"] is True

        # And the next request is not stale: a scope is not a cache.
        assert client.get("/probe").json()["host"] == "reloaded-mid-request"


def case_a_missing_scope_is_refused(
    driver: Driver, wiring: Wiring, config_file: Path
) -> None:
    """A scoped read outside a request raises rather than answering."""
    from dynamic_config_web import OutsideRequestScopeError, current

    try:
        current(wiring.configs[0])
    except OutsideRequestScopeError:
        return

    raise AssertionError("a read outside a request should have been refused")


def case_the_watcher_is_paired_with_the_app(
    driver: Driver, wiring: Wiring, config_file: Path
) -> None:
    """Running while the application serves, and stopped after it.

    Not "started when the object is built": an ASGI adapter arms in the
    lifespan and a WSGI one on its first request, because WSGI has no
    moment that is both after the fork and before the first request. What
    both have to promise is that a serving application is watching, and
    that nothing is left running afterwards.
    """
    config = wiring.configs[0]

    assert holders(config) == 0, "something was already holding it"

    with driver.client(wiring) as client:
        assert client.get("/probe").status_code == 200
        assert holders(config) >= 1, "it served a request without being wired"

    assert holders(config) == 0, "the application left a watcher running"


def case_building_the_app_twice_does_not_collide(
    driver: Driver, wiring: Wiring, config_file: Path
) -> None:
    """`uvicorn --reload`, and a test suite: many apps, one configuration."""
    with driver.client(wiring) as first:
        assert first.get("/probe").status_code == 200

    with driver.client(wiring) as second:
        assert second.get("/probe").status_code == 200

    # Nested, which is what a test that builds a client inside another's
    # block does.
    with driver.client(wiring) as outer, driver.client(wiring) as inner:
        assert outer.get("/probe").status_code == 200
        assert inner.get("/probe").status_code == 200


def case_healthz_is_liveness(driver: Driver, wiring: Wiring, config_file: Path) -> None:
    """200 even when the configuration is in trouble.

    A process that cannot reload should stop receiving traffic, not be
    restarted into reading the same broken file.
    """
    with driver.client(wiring) as client:
        assert client.get("/healthz").status_code == 200

        config_file.write_text('[db]\nhost = "h"\nport = "not a number"\n')

        with suppress(Exception):  # the refusal is the point
            wiring.configs[0].reload()

        assert client.get("/healthz").status_code == 200
        assert client.get("/probe").status_code == 200, "and it is still serving"


def case_readyz_answers_the_other_question(
    driver: Driver, wiring: Wiring, config_file: Path
) -> None:
    """503 before the first load, 200 after, 503 once reloads start failing."""
    config = wiring.configs[0]

    with driver.client(wiring) as client:
        assert client.get("/readyz").status_code == 200

        config_file.write_text('[db]\nhost = "h"\nport = "not a number"\n')

        with suppress(Exception):  # the refusal is the point
            config.reload()

        answer = client.get("/readyz")

        assert answer.status_code == 503

        body = answer.json()

        assert body["configs"]["db"]["installed"] is True
        assert body["configs"]["db"]["healthy"] is False
        # Serving, but the reloads are failing: that is `degraded`, and it
        # is a different sentence from "never managed to load".
        assert body["status"] == "degraded"

        # Fixed, and ready again.
        config_file.write_text('[db]\nhost = "h"\nport = 5433\n')
        config.reload()

        assert client.get("/readyz").status_code == 200


def case_metrics_render_and_carry_no_value(
    driver: Driver, wiring: Wiring, config_file: Path
) -> None:
    """The engine's names, the exposition media type, and no configuration."""
    config_file.write_text(
        '[db]\nhost = "hunter2-do-not-log"\nport = 4242\npool_size = 999\n'
    )
    wiring.configs[0].reload()

    with driver.client(wiring) as client:
        answer = client.get("/metrics")

        assert answer.status_code == 200

        body = answer.text

        assert "dynamic_config_installs_total" in body
        assert 'config="db"' in body
        assert "hunter2" not in body
        assert "4242" not in body
        assert str(config_file) not in body


def case_diagnostics_are_absent_without_a_guard(
    driver: Driver, wiring: Wiring, config_file: Path
) -> None:
    """Not mounted, rather than mounted and refusing.

    A 403 tells a scanner the route is there. The default leaves nothing
    to find.
    """
    with driver.client(wiring) as client:
        assert client.get("/_config/check").status_code == 404
        assert client.get("/_config/explain/port").status_code == 404


def case_diagnostics_answer_behind_a_guard(
    driver: Driver, wiring: Wiring, config_file: Path
) -> None:
    """With a guard: the right token answers, the wrong one does not."""
    from dynamic_config_web import token_guard

    with driver.client(wiring, guard=token_guard("s3cret")) as client:
        allowed = {"x-config-token": "s3cret"}

        answer = client.get("/_config/check", headers=allowed)

        assert answer.status_code == 200
        assert answer.json()["db"]["clean"] is True

        explained = client.get("/_config/explain/port", headers=allowed)

        assert explained.status_code == 200
        assert "5432" in explained.text

        refused = client.get("/_config/check", headers={"x-config-token": "wrong"})

        # Each adapter follows its framework's own convention for a refusal:
        # 404 where the routes are plain views, 403 from DRF's permission
        # layer, 401 from django-ninja's authentication. What is common to
        # all three is the case above this one: with no guard configured the
        # routes are not registered at all.
        assert refused.status_code in (401, 403, 404)


def case_a_pinned_value_is_isolated(
    driver: Driver, wiring: Wiring, config_file: Path
) -> None:
    """The test door: pinned inside the block, gone after it."""
    from dynamic_config_web import pinned

    with driver.client(wiring) as client:
        with pinned(wiring.configs[0], pool_size=1):
            assert client.get("/probe").json()["pool"] == 1

        assert client.get("/probe").json()["pool"] == 8


def case_a_watched_file_reaches_the_handler(
    driver: Driver, watched: Wiring, config_file: Path
) -> None:
    """The whole point, end to end: edit the file, the next request moves."""
    config = watched.configs[0]

    with driver.client(watched) as client:
        assert client.get("/probe").json()["host"] == "db.internal"

        config_file.write_text('[db]\nhost = "edited"\nport = 5432\npool_size = 8\n')

        assert _settle(lambda: config.generation > 1), "the watcher never fired"
        assert client.get("/probe").json()["host"] == "edited"


#: Every case, by name — an adapter parametrises over this and nothing
#: else, so a case added here is a case every adapter must answer.
_CASES = (
    case_a_request_reads_the_configuration,
    case_a_request_never_tears_across_a_reload,
    case_a_missing_scope_is_refused,
    case_the_watcher_is_paired_with_the_app,
    case_building_the_app_twice_does_not_collide,
    case_healthz_is_liveness,
    case_readyz_answers_the_other_question,
    case_metrics_render_and_carry_no_value,
    case_diagnostics_are_absent_without_a_guard,
    case_diagnostics_answer_behind_a_guard,
    case_a_pinned_value_is_isolated,
    case_a_watched_file_reaches_the_handler,
)


def cases(driver: Driver) -> list[Any]:
    """The cases this driver has to pass, minus the ones it cannot."""
    skipped = set()

    if not driver.scope_is_automatic:
        skipped.add(case_a_missing_scope_is_refused)

    return [case for case in _CASES if case not in skipped]


def needs_watching(case: Any) -> bool:
    """Whether a case wants the wiring that actually watches files."""
    return case is case_a_watched_file_reaches_the_handler


def name_of(case: Any) -> str:
    """The case's name, for the test id."""
    return case.__name__.removeprefix("case_")


def run(case: Any, driver: Driver, wiring: Wiring, config_file: Path) -> None:
    """Runs one case against one driver."""
    case(driver, wiring, config_file)


def config_of(wiring: Wiring) -> DynamicConfig[Any]:
    """The single configuration a driver's application serves."""
    return wiring.configs[0]
