"""Robyn, against the shared contract.

Robyn's own `TestClient` runs the pipeline in process — before middleware,
handler, after middleware — without starting the Rust server, which is what
makes this suite applicable at all. What it does *not* run is the startup
and shutdown events, so the driver calls them, exactly as it would if it
were the server.

`scope_is_automatic` is `False` here, and that is the finding rather than a
workaround: a token set in Robyn's before-request middleware is not visible
in the handler, so this adapter's scope lives on the handler instead. The
one case that asserts an automatic scope is skipped, and the eleven that
assert what a request actually sees are not.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable

import pytest

pytest.importorskip("robyn")

from robyn import Events, Robyn

# Aliased: pytest tries to collect anything named `Test*` that it finds
# in a test module, and this one has an `__init__`.
from robyn.testing import TestClient as RobynClient

from dynamic_config_web import Wiring, current
from dynamic_config_web.robyn import scoped, setup, snapshot
from helpers import Database

from . import suite

_FILES: dict[str, str] = {}


class Client:
    """Robyn's test client, in the shape the suite asks for."""

    def __init__(self, inner: RobynClient) -> None:
        self._inner = inner

    def get(self, path: str, headers: dict[str, str] | None = None) -> Any:
        return self._inner.get(path, headers=headers or {})


class RobynDriver:
    """What the shared suite needs to know about Robyn."""

    name = "robyn"

    #: Robyn's middleware and handler do not share a context; the scope is
    #: opened by `@scoped` on the handler. See the module docstring.
    scope_is_automatic = False

    @contextmanager
    def client(
        self,
        wiring: Wiring,
        *,
        guard: Any = None,
        stale_after: float | None = None,
    ) -> Iterator[Client]:
        """An application over `wiring`, with its events run."""
        app = Robyn(__file__)
        config = wiring.configs[0]

        setup(app, wiring, guard=guard, stale_after=stale_after)

        @app.get("/probe")
        @scoped
        async def probe(request: Any) -> dict[str, Any]:
            del request

            db: Database = snapshot()

            return {"host": db.host, "port": db.port, "pool": db.pool_size}

        @app.get("/tear")
        @scoped
        async def tear(request: Any) -> dict[str, Any]:
            """Reads, reloads underneath itself, and reads again."""
            del request

            first = current(config)

            Path(_FILES["db"]).write_text(
                '[db]\nhost = "reloaded-mid-request"\nport = 5432\npool_size = 8\n'
            )
            config.reload()

            second = current(config)

            return {
                "first": first.host,
                "second": second.host,
                "same_object": first is second,
            }

        client = RobynClient(app)

        # What the server does around the request loop, and what the test
        # client leaves to the caller.
        _fire(app, Events.STARTUP)

        try:
            with client:
                yield Client(client)
        finally:
            _fire(app, Events.SHUTDOWN)


def _fire(app: Robyn, event: Events) -> None:
    """Runs one of Robyn's lifecycle handlers, sync or async."""
    import asyncio

    registered = app.event_handlers.get(event)

    if registered is None:
        return

    result = registered.handler()

    if asyncio.iscoroutine(result):
        asyncio.run(result)


@pytest.fixture
def _record(config_file: Path) -> Iterator[None]:
    _FILES["db"] = str(config_file)

    yield

    _FILES.clear()


@pytest.mark.usefixtures("_record")
@pytest.mark.parametrize("case", suite.cases(RobynDriver()), ids=suite.name_of)
def test_robyn_conformance(
    case: Callable[..., None],
    config_file: Path,
    dynamic_config_wiring: Callable[..., Wiring],
    config: Any,
) -> None:
    """Every case in the shared contract, against Robyn."""
    wiring = dynamic_config_wiring(
        config, watch=suite.needs_watching(case), start=False
    )

    case(RobynDriver(), wiring, config_file)
