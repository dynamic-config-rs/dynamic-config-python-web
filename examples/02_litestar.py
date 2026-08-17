"""Litestar, wired with a plugin.

    pip install litestar
    python examples/02_litestar.py

`DynamicConfigPlugin` adds four things through `on_app_init`: the lifespan
that loads and watches, a dependency under the configuration's key, the
health routes, and the middleware that gives each request one reading.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from _shared import Database, show, workspace
from dynamic_config import DynamicConfig
from dynamic_config_web import token_guard
from dynamic_config_web.litestar import DynamicConfigPlugin, NamedDependency

try:
    from litestar import Litestar, get
    from litestar.testing import TestClient
except ImportError:  # pragma: no cover - the example says how to fix it
    raise SystemExit("this example needs Litestar: pip install litestar") from None


def build(path: Path) -> tuple[Litestar, DynamicConfig[Database]]:
    """The application and its configuration."""
    config = DynamicConfig(Database, key="db").file(str(path)).env("APP_")

    @get("/", sync_to_thread=False)
    def index(db: NamedDependency[Database]) -> dict[str, Any]:
        """`db` is the section key, and the model arrives by that name."""
        return {"host": db.host, "port": db.port, "pool": db.pool.max_size}

    app = Litestar(
        [index],
        plugins=[
            DynamicConfigPlugin(config, debounce=0.05, guard=token_guard("s3cret"))
        ],
    )

    return app, config


def main() -> None:
    """Runs the Litestar example end to end."""
    with workspace() as path:
        app, config = build(path)

        # Litestar's `TestClient` runs the lifespan on entry, which is where
        # the load and the watcher are.
        with TestClient(app=app) as client:
            show("serving")
            print(f"  GET /        → {client.get('/').json()}")

            show("the health surface")
            print(f"  GET /healthz → {client.get('/healthz').status_code}")

            ready = client.get("/readyz")
            print(f"  GET /readyz  → {ready.status_code} {ready.json()['status']}")
            print(f"  GET /metrics → {client.get('/metrics').status_code}")

            show("a deployment edits the file")
            path.write_text('[db]\nhost = "db.replica"\nport = 6543\n')
            config.reload()

            print(f"  GET /        → {client.get('/').json()}")

            show("a bad edit changes nothing")
            path.write_text('[db]\nhost = "db.replica"\nport = "as many as it takes"\n')

            try:
                config.reload()
            except Exception as refused:
                print(f"  refused: {type(refused).__name__}")

            print(f"  GET /        → {client.get('/').json()}   (still serving)")

            ready = client.get("/readyz")
            print(f"  GET /readyz  → {ready.status_code} {ready.json()['status']}")

            show("diagnostics, for whoever holds the token")
            print(f"  no token  → {client.get('/_config/check').status_code}")

            answer = client.get(
                "/_config/explain/port", headers={"x-config-token": "s3cret"}
            )
            print(f"  with one  → {answer.status_code}")

            for line in answer.text.splitlines()[:4]:
                print(f"    {line}")


if __name__ == "__main__":
    main()
