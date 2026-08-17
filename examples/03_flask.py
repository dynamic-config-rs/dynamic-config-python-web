"""Flask, wired with an extension — and the habit it replaces.

    pip install flask
    python examples/03_flask.py

The point of this one is the thing it does *not* do: nothing is copied into
`app.config`. A value copied there at startup is frozen at the moment it was
copied, which is the bug this package exists to prevent. `snapshot()` is the
read, and it answers the model this request began with.

WSGI has no lifespan, so the watcher arms on the first request each process
serves — after a fork by construction, which is what makes it right under
`gunicorn --preload`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from _shared import Database, show, workspace
from dynamic_config import DynamicConfig
from dynamic_config_web import token_guard
from dynamic_config_web.flask import DynamicConfigExtension, snapshot

try:
    from flask import Flask
except ImportError:  # pragma: no cover - the example says how to fix it
    raise SystemExit("this example needs Flask: pip install flask") from None


def build(path: Path) -> tuple[Flask, DynamicConfig[Database], Any]:
    """The application, its configuration, and the extension."""
    config = DynamicConfig(Database, key="db").file(str(path)).env("APP_")
    app = Flask(__name__)

    extension = DynamicConfigExtension(
        app, config, debounce=0.05, guard=token_guard("s3cret")
    )

    @app.get("/")
    def index() -> dict[str, Any]:
        """Read here, not from `app.config` — a copy never reloads."""
        db: Database = snapshot()

        return {"host": db.host, "port": db.port, "pool": db.pool.max_size}

    return app, config, extension


def main() -> None:
    """Runs the Flask example end to end."""
    with workspace() as path:
        app, config, extension = build(path)

        try:
            with app.test_client() as client:
                show("serving")
                print(f"  GET /        → {client.get('/').get_json()}")
                print(f"  watching     → {extension.wiring.watching}")

                show("the health surface")
                print(f"  GET /healthz → {client.get('/healthz').status_code}")

                ready = client.get("/readyz")
                print(
                    f"  GET /readyz  → {ready.status_code} {ready.get_json()['status']}"
                )
                print(f"  GET /metrics → {client.get('/metrics').status_code}")

                show("a deployment edits the file")
                path.write_text('[db]\nhost = "db.replica"\nport = 6543\n')
                config.reload()

                print(f"  GET /        → {client.get('/').get_json()}")

                show("a bad edit changes nothing")
                path.write_text(
                    '[db]\nhost = "db.replica"\nport = "as many as it takes"\n'
                )

                try:
                    config.reload()
                except Exception as refused:
                    print(f"  refused: {type(refused).__name__}")

                answer = client.get("/").get_json()
                print(f"  GET /        → {answer}   (still serving)")

                ready = client.get("/readyz")
                print(
                    f"  GET /readyz  → {ready.status_code} {ready.get_json()['status']}"
                )

                show("diagnostics, for whoever holds the token")
                print(f"  no token  → {client.get('/_config/check').status_code}")

                answer = client.get(
                    "/_config/explain/port", headers={"x-config-token": "s3cret"}
                )
                print(f"  with one  → {answer.status_code}")

                for line in answer.get_data(as_text=True).splitlines()[:4]:
                    print(f"    {line}")
        finally:
            # WSGI has no shutdown event, so a script that has finished
            # serving says so itself. A server exits instead.
            extension.stop()


if __name__ == "__main__":
    main()
