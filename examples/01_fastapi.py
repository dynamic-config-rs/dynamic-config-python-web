"""FastAPI, wired in three lines.

    pip install fastapi httpx
    python examples/01_fastapi.py

What `setup` is: the lifespan that loads and watches, the middleware that
gives each request one reading, and the health surface. What is left for
the application is the part that is the application — a dependency and a
handler.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from _shared import Database, show, workspace
from dynamic_config import DynamicConfig
from dynamic_config_web import token_guard
from dynamic_config_web.fastapi import config_dependency, setup

try:
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover - the example says how to fix it
    raise SystemExit("this example needs FastAPI: pip install fastapi httpx") from None


def build(path: Path) -> tuple[FastAPI, DynamicConfig[Database], Any]:
    """The application, its configuration, and the dependency to override."""
    config = DynamicConfig(Database, key="db").file(str(path)).env("APP_")
    app = FastAPI()

    # Everything a service needs around the configuration, in one call:
    # the lifespan, the request scope, /healthz, /readyz, /metrics — and,
    # because a guard is given, /_config/explain and /_config/check.
    setup(app, config, debounce=0.05, guard=token_guard("s3cret"))

    # The same object every time, which is what makes it overridable from
    # a test that never saw the application being built.
    database = config_dependency(config)

    @app.get("/")
    def index(db: Database = Depends(database)) -> dict[str, Any]:
        """Read once, at the top, and use that value for the whole request."""
        return {"host": db.host, "port": db.port, "pool": db.pool.max_size}

    return app, config, database


def main() -> None:
    """Runs the FastAPI example end to end."""
    with workspace() as path:
        app, config, database = build(path)

        # The lifespan runs on entry, which is where the load and the
        # watcher happen — and on exit, which is where the watcher stops.
        with TestClient(app) as client:
            show("serving")
            print(f"  GET /        → {client.get('/').json()}")

            show("the health surface")
            print(f"  GET /healthz → {client.get('/healthz').status_code}")

            ready = client.get("/readyz")
            print(f"  GET /readyz  → {ready.status_code} {ready.json()['status']}")

            body = client.get("/metrics").text
            names = sorted(
                line.split("{")[0].split(" ")[0]
                for line in body.splitlines()
                if line and not line.startswith("#")
            )
            print(f"  GET /metrics → {len(set(names))} series")

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
            print(f"  GET /healthz → {client.get('/healthz').status_code}   (liveness)")

            show("diagnostics, for whoever holds the token")
            print(f"  no token  → {client.get('/_config/check').status_code}")

            answer = client.get(
                "/_config/explain/port", headers={"x-config-token": "s3cret"}
            )
            print(f"  with one  → {answer.status_code}")

            for line in answer.text.splitlines()[:4]:
                print(f"    {line}")

        show("overriding it in a test is FastAPI's own mechanism")
        # The file is put back first, deliberately: the block above left it
        # broken, and a lifespan that cannot load *refuses to start* — which
        # is the right answer at startup and the wrong surprise here.
        path.write_text('[db]\nhost = "db.replica"\nport = 6543\n')
        app.dependency_overrides[database] = lambda: Database(host="fixture")

        with TestClient(app) as client:
            print(f"  GET /        → {client.get('/').json()}")

        app.dependency_overrides.clear()


if __name__ == "__main__":
    main()
