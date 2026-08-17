"""django-ninja: the health surface as a `Router` on a `NinjaAPI`.

    pip install django django-ninja
    python examples/08_django_ninja.py

The Django adapter supplies everything that is not routing — `AppConfig.ready`
loads and watches, the middleware opens the request scope — so a Ninja project
adds one router and reads with `snapshot()`, exactly as a plain view does.

A real project writes:

    INSTALLED_APPS = [..., "dynamic_config_web.django", "ninja"]
    MIDDLEWARE = ["dynamic_config_web.django.middleware.DynamicConfigMiddleware", ...]
    DYNAMIC_CONFIG = {"target": "myproject.config:database", "guard": "..."}

    api = NinjaAPI()
    api.add_router("/", router())
"""

from __future__ import annotations

from typing import Any

from _shared import Database, show, workspace
from dynamic_config import DynamicConfig
from dynamic_config_web import token_guard

try:
    import django
    from django.conf import settings
except ImportError:  # pragma: no cover - the example says how to fix it
    raise SystemExit("this example needs Django: pip install django") from None

#: Rewritten in `main`, once the API exists.
urlpatterns: list[Any] = []

#: What `DYNAMIC_CONFIG` points at, and who may read the diagnostics.
database: DynamicConfig[Database]
guard = token_guard("s3cret")


def configure() -> None:
    """What a `settings.py` would say, said in memory."""
    if settings.configured:
        return

    settings.configure(
        DEBUG=False,
        SECRET_KEY="example",
        ALLOWED_HOSTS=["*"],
        ROOT_URLCONF=__name__,
        INSTALLED_APPS=["dynamic_config_web.django", "ninja"],
        MIDDLEWARE=["dynamic_config_web.django.middleware.DynamicConfigMiddleware"],
        DATABASES={},
        USE_TZ=True,
        DYNAMIC_CONFIG={
            "target": f"{__name__}:database",
            "debounce": 0.05,
            "guard": f"{__name__}:guard",
        },
    )


def main() -> None:
    """Runs the django-ninja example end to end."""
    global database

    with workspace() as path:
        database = DynamicConfig(Database, key="db").file(str(path)).env("APP_")

        configure()
        django.setup()

        from django.test import Client
        from django.urls import path as route
        from ninja import NinjaAPI

        from dynamic_config_web.django import snapshot
        from dynamic_config_web.django.ninja import router

        api = NinjaAPI()

        @api.get("/")
        def index(request: Any) -> dict[str, Any]:
            """`snapshot()` needs no argument and no `request`."""
            del request

            db: Database = snapshot()

            return {"host": db.host, "port": db.port, "pool": db.pool.max_size}

        # The health, metrics and — because a guard is configured —
        # diagnostics operations, mounted at the API's root.
        api.add_router("/", router())

        urlpatterns[:] = [route("", api.urls)]

        client = Client()

        show("serving")
        print(f"  GET /        → {client.get('/').json()}")

        show("the health surface")
        print(f"  GET /healthz → {client.get('/healthz').status_code}")

        ready = client.get("/readyz")
        print(f"  GET /readyz  → {ready.status_code} {ready.json()['status']}")
        print(f"  GET /metrics → {client.get('/metrics').status_code}")

        show("a deployment edits the file")
        path.write_text('[db]\nhost = "db.replica"\nport = 6543\n')
        database.reload()

        print(f"  GET /        → {client.get('/').json()}")

        show("a bad edit changes nothing")
        path.write_text('[db]\nhost = "db.replica"\nport = "as many as it takes"\n')

        try:
            database.reload()
        except Exception as refused:
            print(f"  refused: {type(refused).__name__}")

        print(f"  GET /        → {client.get('/').json()}   (still serving)")

        ready = client.get("/readyz")
        print(f"  GET /readyz  → {ready.status_code} {ready.json()['status']}")

        show("diagnostics, for whoever holds the token")
        # 401 rather than the plain views' 404: django-ninja's authentication
        # answers, and each adapter keeps its framework's convention.
        print(f"  no token  → {client.get('/_config/check').status_code}")

        answer = client.get(
            "/_config/explain/port", headers={"x-config-token": "s3cret"}
        )
        print(f"  with one  → {answer.status_code}")

        for line in answer.content.decode().splitlines()[:4]:
            print(f"    {line}")


if __name__ == "__main__":
    main()
