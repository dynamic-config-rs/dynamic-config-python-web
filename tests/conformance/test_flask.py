"""Flask, against the shared contract.

Flask's test client answers a `Response` whose `json` is a property, so the
driver wraps it: the suite asks every framework the same three questions
(`status_code`, `json()`, `text`) and each driver translates.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable

import pytest

pytest.importorskip("flask")

from flask import Flask

from dynamic_config_web import Wiring, current
from dynamic_config_web.flask import DynamicConfigExtension, snapshot
from helpers import Database

from . import suite

_FILES: dict[str, str] = {}


class Answer:
    """One framework's response, in the shape the suite asks for."""

    def __init__(self, response: Any) -> None:
        self._response = response
        self.status_code = response.status_code

    def json(self) -> Any:
        return self._response.get_json()

    @property
    def text(self) -> str:
        return self._response.get_data(as_text=True)  # type: ignore[no-any-return]


class Client:
    """A test client that answers `Answer`s."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    def get(self, path: str, headers: dict[str, str] | None = None) -> Answer:
        return Answer(self._inner.get(path, headers=headers or {}))


class FlaskDriver:
    """What the shared suite needs to know about Flask."""

    name = "flask"
    scope_is_automatic = True

    @contextmanager
    def client(
        self,
        wiring: Wiring,
        *,
        guard: Any = None,
        stale_after: float | None = None,
    ) -> Iterator[Client]:
        """An application over `wiring`, with the extension installed."""
        app = Flask(__name__)
        config = wiring.configs[0]

        extension = DynamicConfigExtension(
            app,
            wiring,
            guard=guard,
            stale_after=stale_after,
        )

        @app.get("/probe")
        def probe() -> dict[str, Any]:
            """Read inside the view, not copied into `app.config`."""
            db: Database = snapshot()

            return {"host": db.host, "port": db.port, "pool": db.pool_size}

        @app.get("/tear")
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

        try:
            with app.test_client() as inner:
                yield Client(inner)
        finally:
            # WSGI has no shutdown, so the extension's own `stop` is what
            # pairs with the block — and it is what the conformance case
            # about a watcher outliving the app asserts.
            extension.stop()


@pytest.fixture
def _record(config_file: Path) -> Iterator[None]:
    _FILES["db"] = str(config_file)

    yield

    _FILES.clear()


@pytest.mark.usefixtures("_record")
@pytest.mark.parametrize("case", suite.cases(FlaskDriver()), ids=suite.name_of)
def test_flask_conformance(
    case: Callable[..., None],
    config_file: Path,
    dynamic_config_wiring: Callable[..., Wiring],
    config: Any,
) -> None:
    """Every case in the shared contract, against Flask."""
    wiring = dynamic_config_wiring(
        config, watch=suite.needs_watching(case), start=False
    )

    case(FlaskDriver(), wiring, config_file)
