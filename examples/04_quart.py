"""Quart: Flask's extension, with the lifespan Flask does not have.

    pip install quart
    python examples/04_quart.py

Same API as the Flask example — an extension, `snapshot()`, nothing in
`app.config` — with one difference that matters. Quart is ASGI, so it has
`while_serving`: a moment after the workers exist and before the first
request. That is where the watcher goes, and it is why this adapter is not
simply Flask's.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from _shared import Database, show, workspace
from dynamic_config import DynamicConfig
from dynamic_config_web import token_guard
from dynamic_config_web.quart import DynamicConfigExtension, snapshot

try:
    from quart import Quart
except ImportError:  # pragma: no cover - the example says how to fix it
    raise SystemExit("this example needs Quart: pip install quart") from None


def build(path: Path) -> tuple[Quart, DynamicConfig[Database], Any]:
    """The application, its configuration, and the extension."""
    config = DynamicConfig(Database, key="db").file(str(path)).env("APP_")
    app = Quart(__name__)

    extension = DynamicConfigExtension(
        app, config, debounce=0.05, guard=token_guard("s3cret")
    )

    @app.get("/")
    async def index() -> dict[str, Any]:
        """The model this request began with, however long the request runs."""
        db: Database = snapshot()

        return {"host": db.host, "port": db.port, "pool": db.pool.max_size}

    return app, config, extension


async def run(path: Path) -> None:
    """Runs the Quart example end to end."""
    app, config, extension = build(path)

    # `startup()` and `shutdown()` are what run `while_serving`; a real
    # deployment gets them from hypercorn or uvicorn.
    await app.startup()

    try:
        client = app.test_client()

        show("serving")
        print(f"  GET /        → {await (await client.get('/')).get_json()}")
        print(f"  watching     → {extension.wiring.watching}")

        show("the health surface")
        print(f"  GET /healthz → {(await client.get('/healthz')).status_code}")

        ready = await client.get("/readyz")
        body = await ready.get_json()
        print(f"  GET /readyz  → {ready.status_code} {body['status']}")
        print(f"  GET /metrics → {(await client.get('/metrics')).status_code}")

        show("a deployment edits the file")
        path.write_text('[db]\nhost = "db.replica"\nport = 6543\n')
        await config.reload_async()

        print(f"  GET /        → {await (await client.get('/')).get_json()}")

        show("a bad edit changes nothing")
        path.write_text('[db]\nhost = "db.replica"\nport = "as many as it takes"\n')

        try:
            await config.reload_async()
        except Exception as refused:
            print(f"  refused: {type(refused).__name__}")

        answer = await client.get("/")
        print(f"  GET /        → {await answer.get_json()}   (still serving)")

        ready = await client.get("/readyz")
        body = await ready.get_json()
        print(f"  GET /readyz  → {ready.status_code} {body['status']}")

        show("diagnostics, for whoever holds the token")
        print(f"  no token  → {(await client.get('/_config/check')).status_code}")

        explained = await client.get(
            "/_config/explain/port", headers={"x-config-token": "s3cret"}
        )
        print(f"  with one  → {explained.status_code}")

        rendered = (await explained.get_data()).decode()

        for line in rendered.splitlines()[:4]:
            print(f"    {line}")
    finally:
        await app.shutdown()


def main() -> None:
    """The synchronous door, so the example runs like the others."""
    with workspace() as path:
        asyncio.run(run(path))


if __name__ == "__main__":
    main()
