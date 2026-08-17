"""Litestar, against the shared contract."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable

import pytest

pytest.importorskip("litestar")

from litestar import Litestar, get
from litestar.testing import TestClient

from dynamic_config_web import Wiring, current
from dynamic_config_web.litestar import (
    DynamicConfigPlugin,
    NamedDependency,
)
from helpers import Database

from . import suite

_FILES: dict[str, str] = {}


class LitestarDriver:
    """What the shared suite needs to know about Litestar."""

    name = "litestar"
    scope_is_automatic = True

    @contextmanager
    def client(
        self,
        wiring: Wiring,
        *,
        guard: Any = None,
        stale_after: float | None = None,
    ) -> Iterator[TestClient[Litestar]]:
        """An application over `wiring`, with its lifespan run."""
        config = wiring.configs[0]

        @get("/probe", sync_to_thread=False)
        def probe(db: NamedDependency[Database]) -> dict[str, Any]:
            """The dependency arrives by name — `db` is the section key."""
            return {"host": db.host, "port": db.port, "pool": db.pool_size}

        @get("/tear", sync_to_thread=False)
        def tear() -> dict[str, Any]:
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

        app = Litestar(
            [probe, tear],
            plugins=[
                DynamicConfigPlugin(
                    wiring,
                    guard=guard,
                    stale_after=stale_after,
                    diagnostics_path="/_config",
                )
            ],
        )

        with TestClient(app=app) as client:
            yield client


@pytest.fixture
def _record(config_file: Path) -> Iterator[None]:
    _FILES["db"] = str(config_file)

    yield

    _FILES.clear()


@pytest.mark.usefixtures("_record")
@pytest.mark.parametrize("case", suite.cases(LitestarDriver()), ids=suite.name_of)
def test_litestar_conformance(
    case: Callable[..., None],
    config_file: Path,
    dynamic_config_wiring: Callable[..., Wiring],
    config: Any,
) -> None:
    """Every case in the shared contract, against Litestar."""
    wiring = dynamic_config_wiring(
        config, watch=suite.needs_watching(case), start=False
    )

    case(LitestarDriver(), wiring, config_file)
