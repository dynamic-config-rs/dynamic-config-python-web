"""FastAPI, against the shared contract.

The driver is the whole adapter-specific part: build an app, run its
lifespan, issue a request. Every assertion is in `suite.py`, which is what
makes "seven frameworks behave the same" a thing this repository checks
rather than a thing its README claims.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from dynamic_config_web import Wiring, current
from dynamic_config_web.fastapi import config_dependency, setup
from helpers import Database

from . import suite


class FastAPIDriver:
    """What the shared suite needs to know about FastAPI."""

    name = "fastapi"
    scope_is_automatic = True

    @contextmanager
    def client(
        self,
        wiring: Wiring,
        *,
        guard: Any = None,
        stale_after: float | None = None,
    ) -> Iterator[TestClient]:
        """An application over `wiring`, with its lifespan run."""
        app = FastAPI()
        config = wiring.configs[0]

        setup(app, wiring, guard=guard, stale_after=stale_after)

        database = config_dependency(config)

        @app.get("/probe")
        def probe(db: Database = Depends(database)) -> dict[str, Any]:
            """The ordinary read: once, at the top, and used throughout."""
            return {"host": db.host, "port": db.port, "pool": db.pool_size}

        @app.get("/tear")
        def tear() -> dict[str, Any]:
            """Reads, reloads underneath itself, and reads again."""
            first = current(config)

            Path(config_file_of(config)).write_text(
                '[db]\nhost = "reloaded-mid-request"\nport = 5432\npool_size = 8\n'
            )
            config.reload()

            second = current(config)

            return {
                "first": first.host,
                "second": second.host,
                "same_object": first is second,
            }

        @app.get("/pair")
        def pair() -> dict[str, Any]:
            """Every configuration in the wiring, read under one scope.

            Reads both, moves both underneath itself, reads both again:
            a scope over several configurations pins all of them, not
            just the first.
            """
            first = {c.key: current(c).host for c in wiring.configs}

            for member in wiring.configs:
                if member.key != "db":
                    member.set_override("host", "moved.internal")
                member.reload()

            second = {c.key: current(c).host for c in wiring.configs}

            return {
                "db_first": first["db"],
                "db_second": second["db"],
                "extra_first": first.get("extra"),
                "extra_second": second.get("extra"),
            }

        with TestClient(app) as client:
            yield client


def config_file_of(config: Any) -> str:
    """The file this configuration reads, for the tearing case."""
    return _FILES[config.key]


#: The driver needs the path the fixture wrote, and a `DynamicConfig` does
#: not hand its sources back out — deliberately, since a source is not a
#: value. The test module keeps the one it made.
_FILES: dict[str, str] = {}


@pytest.fixture
def _record(config_file: Path) -> Iterator[None]:
    _FILES["db"] = str(config_file)

    yield

    _FILES.clear()


@pytest.mark.usefixtures("_record")
@pytest.mark.parametrize("case", suite.cases(FastAPIDriver()), ids=suite.name_of)
def test_fastapi_conformance(
    case: Callable[..., None],
    config_file: Path,
    dynamic_config_wiring: Callable[..., Wiring],
    config: Any,
) -> None:
    """Every case in the shared contract, against FastAPI."""
    wiring = dynamic_config_wiring(
        config,
        watch=suite.needs_watching(case),
        start=False,
    )

    case(FastAPIDriver(), wiring, config_file)
