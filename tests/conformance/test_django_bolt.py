"""django-bolt, against the shared contract.

Its `TestClient` runs the real Actix stack in process and — unusually among
test clients — runs the lifespan too, so the driver has nothing to fake.
Django must be configured before a `BoltAPI` exists, which is what the
shared bootstrap is for.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable

import pytest

pytest.importorskip("django_bolt")

import django_bootstrap

django_bootstrap.bootstrap()

from django_bolt.testing import TestClient as BoltClient  # noqa: E402

from dynamic_config_web import Wiring, current  # noqa: E402
from dynamic_config_web.django_bolt import api, snapshot  # noqa: E402
from helpers import Database  # noqa: E402

from . import suite  # noqa: E402

_FILES: dict[str, str] = {}


class DjangoBoltDriver:
    """What the shared suite needs to know about django-bolt."""

    name = "django-bolt"
    scope_is_automatic = True

    @contextmanager
    def client(
        self,
        wiring: Wiring,
        *,
        guard: Any = None,
        stale_after: float | None = None,
    ) -> Iterator[Any]:
        """An API over `wiring`, with its lifespan run."""
        config = wiring.configs[0]
        bolt = api(wiring, guard=guard, stale_after=stale_after)

        @bolt.get("/probe")
        async def probe() -> dict[str, Any]:
            db: Database = snapshot()

            return {"host": db.host, "port": db.port, "pool": db.pool_size}

        @bolt.get("/tear")
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

        with BoltClient(bolt) as client:
            yield client


@pytest.fixture
def _record(config_file: Path) -> Iterator[None]:
    _FILES["db"] = str(config_file)

    yield

    _FILES.clear()


@pytest.mark.usefixtures("_record")
@pytest.mark.parametrize("case", suite.cases(DjangoBoltDriver()), ids=suite.name_of)
def test_django_bolt_conformance(
    case: Callable[..., None],
    config_file: Path,
    dynamic_config_wiring: Callable[..., Wiring],
    config: Any,
) -> None:
    """Every case in the shared contract, against django-bolt."""
    wiring = dynamic_config_wiring(
        config, watch=suite.needs_watching(case), start=False
    )

    case(DjangoBoltDriver(), wiring, config_file)
