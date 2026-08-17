"""Robyn: startup and shutdown handlers, and a scope you ask for.

    pip install robyn
    python examples/06_robyn.py

**Experimental.** The one thing to read before using it: `@scoped` is
required on every handler that reads configuration. Robyn calls its
before-request middleware and then calls the handler, and a `contextvars`
token set in the first is not visible in the second — so this adapter puts
the scope on the handler, where it demonstrably holds.

A handler that forgets it raises `OutsideRequestScopeError` rather than
quietly reading a different generation halfway through, which is the trade
this package makes everywhere.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from _shared import Database, show, workspace
from dynamic_config import DynamicConfig
from dynamic_config_web import OutsideRequestScopeError, token_guard
from dynamic_config_web.robyn import scoped, setup, snapshot

try:
    from robyn import Events, Robyn
    from robyn.testing import TestClient
except ImportError:  # pragma: no cover - the example says how to fix it
    raise SystemExit("this example needs Robyn: pip install robyn") from None


def build(path: Path) -> tuple[Robyn, DynamicConfig[Database]]:
    """The application and its configuration."""
    config = DynamicConfig(Database, key="db").file(str(path)).env("APP_")
    app = Robyn(__file__)

    setup(app, config, debounce=0.05, guard=token_guard("s3cret"))

    @app.get("/")
    @scoped
    async def index(request: Any) -> dict[str, Any]:
        """`@scoped` under the route decorator, so Robyn registers the wrapper."""
        del request

        db: Database = snapshot()

        return {"host": db.host, "port": db.port, "pool": db.pool.max_size}

    @app.get("/unscoped")
    async def unscoped(request: Any) -> dict[str, Any]:
        """Deliberately missing the decorator — see what it answers."""
        del request

        try:
            snapshot()
        except OutsideRequestScopeError:
            return {"refused": True}

        return {"refused": False}

    return app, config


def fire(app: Robyn, event: Events) -> None:
    """Runs a lifecycle handler, which a real server does around serving."""
    registered = app.event_handlers.get(event)

    if registered is None:
        return

    result = registered.handler()

    if asyncio.iscoroutine(result):
        asyncio.run(result)


def main() -> None:
    """Runs the Robyn example end to end."""
    with workspace() as path:
        app, config = build(path)

        fire(app, Events.STARTUP)

        try:
            with TestClient(app) as client:
                show("serving")
                print(f"  GET /          → {client.get('/').json()}")
                print(f"  GET /unscoped  → {client.get('/unscoped').json()}")

                show("the health surface")
                print(f"  GET /healthz   → {client.get('/healthz').status_code}")

                ready = client.get("/readyz")
                status = ready.json()["status"]
                print(f"  GET /readyz    → {ready.status_code} {status}")
                print(f"  GET /metrics   → {client.get('/metrics').status_code}")

                show("a deployment edits the file")
                path.write_text('[db]\nhost = "db.replica"\nport = 6543\n')
                config.reload()

                print(f"  GET /          → {client.get('/').json()}")

                show("a bad edit changes nothing")
                path.write_text(
                    '[db]\nhost = "db.replica"\nport = "as many as it takes"\n'
                )

                try:
                    config.reload()
                except Exception as refused:
                    print(f"  refused: {type(refused).__name__}")

                print(f"  GET /          → {client.get('/').json()}   (still serving)")

                ready = client.get("/readyz")
                status = ready.json()["status"]
                print(f"  GET /readyz    → {ready.status_code} {status}")

                show("diagnostics, for whoever holds the token")
                print(f"  no token  → {client.get('/_config/check').status_code}")

                answer = client.get(
                    "/_config/explain/port", headers={"x-config-token": "s3cret"}
                )
                print(f"  with one  → {answer.status_code}")

                for line in answer.text.splitlines()[:4]:
                    print(f"    {line}")
        finally:
            fire(app, Events.SHUTDOWN)


if __name__ == "__main__":
    main()
