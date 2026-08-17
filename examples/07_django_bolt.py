"""django-bolt: the API built wired, because its seams are constructor arguments.

    pip install "django-bolt>=0.10" django       # Python 3.12+
    python examples/07_django_bolt.py

**Experimental.** django-bolt takes its lifespan and its middleware in
`BoltAPI(...)`, so this adapter offers a factory rather than the
`setup(app, config)` the others do: `api(config)` builds the `BoltAPI` with
the lifespan, the request scope and the health routes already on it, and
passes every other keyword straight through.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from _shared import Database, show, workspace
from dynamic_config import DynamicConfig
from dynamic_config_web import token_guard

try:
    import django
    from django.conf import settings
except ImportError:  # pragma: no cover - the example says how to fix it
    raise SystemExit("this example needs Django: pip install django") from None

if not settings.configured:
    # django-bolt is a Django application: settings first, then the API.
    settings.configure(
        DEBUG=False,
        SECRET_KEY="example",
        ALLOWED_HOSTS=["*"],
        INSTALLED_APPS=[],
        DATABASES={},
        USE_TZ=True,
    )
    django.setup()

try:
    from django_bolt.testing import TestClient
except ImportError:  # pragma: no cover - the example says how to fix it
    raise SystemExit(
        'this example needs django-bolt: pip install "django-bolt>=0.10"'
    ) from None

from dynamic_config_web.django_bolt import api, snapshot


def build(path: Path) -> tuple[Any, DynamicConfig[Database]]:
    """The API and its configuration."""
    config = DynamicConfig(Database, key="db").file(str(path)).env("APP_")

    bolt = api(config, debounce=0.05, guard=token_guard("s3cret"))

    @bolt.get("/")
    async def index() -> dict[str, Any]:
        """The middleware opened the scope; this reads out of it."""
        db: Database = snapshot()

        return {"host": db.host, "port": db.port, "pool": db.pool.max_size}

    return bolt, config


def main() -> None:
    """Runs the django-bolt example end to end."""
    with workspace() as path:
        bolt, config = build(path)

        # Its test client runs the lifespan, which is where the load and
        # the watcher are.
        with TestClient(bolt) as client:
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
