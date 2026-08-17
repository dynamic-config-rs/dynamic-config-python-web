"""Quart, against the shared contract.

Quart's test client is asynchronous and its lifespan runs on a loop, so the
driver keeps one loop for the whole block and drives it — the suite stays
synchronous, and every framework answers the same twelve questions.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable

import pytest

pytest.importorskip("quart")

from quart import Quart

from dynamic_config_web import Wiring, current
from dynamic_config_web.quart import DynamicConfigExtension, snapshot
from helpers import Database

from . import suite

_FILES: dict[str, str] = {}


class Answer:
    """One framework's response, in the shape the suite asks for."""

    def __init__(self, status_code: int, body: bytes, parsed: Any) -> None:
        self.status_code = status_code
        self._body = body
        self._parsed = parsed

    def json(self) -> Any:
        return self._parsed

    @property
    def text(self) -> str:
        return self._body.decode()


class Client:
    """The async test client, driven from a synchronous suite."""

    def __init__(self, loop: asyncio.AbstractEventLoop, inner: Any) -> None:
        self._loop = loop
        self._inner = inner

    def get(self, path: str, headers: dict[str, str] | None = None) -> Answer:
        async def request() -> Answer:
            response = await self._inner.get(path, headers=headers or {})
            body = await response.get_data()

            try:
                parsed = await response.get_json()
            except Exception:
                parsed = None

            return Answer(response.status_code, body, parsed)

        return self._loop.run_until_complete(request())


class QuartDriver:
    """What the shared suite needs to know about Quart."""

    name = "quart"
    scope_is_automatic = True

    @contextmanager
    def client(
        self,
        wiring: Wiring,
        *,
        guard: Any = None,
        stale_after: float | None = None,
    ) -> Iterator[Client]:
        """An application over `wiring`, with `while_serving` run."""
        app = Quart(__name__)
        config = wiring.configs[0]

        DynamicConfigExtension(app, wiring, guard=guard, stale_after=stale_after)

        @app.get("/probe")
        async def probe() -> dict[str, Any]:
            db: Database = snapshot()

            return {"host": db.host, "port": db.port, "pool": db.pool_size}

        @app.get("/tear")
        async def tear() -> dict[str, Any]:
            """Reads, reloads underneath itself, and reads again."""
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

        loop = asyncio.new_event_loop()

        try:
            # `startup()` and `shutdown()` are what run `while_serving`,
            # which is where this adapter loads and watches.
            loop.run_until_complete(app.startup())

            try:
                yield Client(loop, app.test_client())
            finally:
                loop.run_until_complete(app.shutdown())
        finally:
            loop.close()


@pytest.fixture
def _record(config_file: Path) -> Iterator[None]:
    _FILES["db"] = str(config_file)

    yield

    _FILES.clear()


@pytest.mark.usefixtures("_record")
@pytest.mark.parametrize("case", suite.cases(QuartDriver()), ids=suite.name_of)
def test_quart_conformance(
    case: Callable[..., None],
    config_file: Path,
    dynamic_config_wiring: Callable[..., Wiring],
    config: Any,
) -> None:
    """Every case in the shared contract, against Quart."""
    wiring = dynamic_config_wiring(
        config, watch=suite.needs_watching(case), start=False
    )

    case(QuartDriver(), wiring, config_file)
