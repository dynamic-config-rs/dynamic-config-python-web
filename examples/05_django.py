"""Django, and Django REST Framework: an app, a middleware, a setting.

    pip install django djangorestframework
    python examples/05_django.py

A real project writes four lines and no more:

    INSTALLED_APPS = [..., "dynamic_config_web.django"]
    MIDDLEWARE = ["dynamic_config_web.django.middleware.DynamicConfigMiddleware", ...]
    DYNAMIC_CONFIG = {"target": "myproject.config:database"}
    urlpatterns = [path("", include("dynamic_config_web.django.urls")), ...]

This script configures Django in memory instead, because an example should
run without a project on disk — but the wiring it exercises is the same one,
including `manage.py configcheck`.

**Do not put configuration in `settings`.** Django loads settings once and
caches them for the life of the process; a value copied in at startup is
frozen there. `DYNAMIC_CONFIG` holds a pointer, and `snapshot()` is the read.
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

#: Rewritten below, because the routes depend on the installation.
urlpatterns: list[Any] = []


def configure() -> None:
    """What a `settings.py` would say, said in memory."""
    if settings.configured:
        return

    installed = ["dynamic_config_web.django"]

    try:
        import rest_framework  # noqa: F401

        installed.append("rest_framework")
    except ImportError:
        pass

    settings.configure(
        DEBUG=False,
        SECRET_KEY="example",
        ALLOWED_HOSTS=["*"],
        ROOT_URLCONF=__name__,
        INSTALLED_APPS=installed,
        MIDDLEWARE=[
            "dynamic_config_web.django.middleware.DynamicConfigMiddleware",
        ],
        DATABASES={},
        USE_TZ=True,
        # Only because this example installs no auth app: DRF resolves
        # `request.user` on every view, and its default anonymous user
        # lives in `django.contrib.auth`. A real project has it already.
        REST_FRAMEWORK={"UNAUTHENTICATED_USER": None},
        # A real project sets this to a dotted path. Here the configuration
        # is built in this module, so the pointer names this module.
        DYNAMIC_CONFIG={
            "target": f"{__name__}:database",
            "debounce": 0.05,
            "guard": f"{__name__}:guard",
        },
    )


#: What `DYNAMIC_CONFIG` points at. Built at import, loaded by `ready()`.
database: DynamicConfig[Database]
guard = token_guard("s3cret")


def main() -> None:
    """Runs the Django example end to end."""
    global database

    with workspace() as path:
        database = DynamicConfig(Database, key="db").file(str(path)).env("APP_")

        configure()
        django.setup()

        from django.http import JsonResponse
        from django.test import Client
        from django.urls import path as route

        from dynamic_config_web.django import snapshot, wiring
        from dynamic_config_web.django.urls import urls as config_urls

        def index(request: Any) -> JsonResponse:
            """`snapshot()` needs no request; `request.dynamic_config` is there too."""
            del request

            db: Database = snapshot()

            return JsonResponse(
                {"host": db.host, "port": db.port, "pool": db.pool.max_size}
            )

        urlpatterns[:] = [*config_urls(), route("", index)]

        client = Client()

        show("serving")
        print(f"  GET /        → {client.get('/').json()}")
        print(f"  watching     → {wiring().watching}")

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
        print(f"  no token  → {client.get('/_config/check').status_code}")

        answer = client.get(
            "/_config/explain/port", headers={"x-config-token": "s3cret"}
        )
        print(f"  with one  → {answer.status_code}")

        for line in answer.content.decode().splitlines()[:4]:
            print(f"    {line}")

        show("manage.py configcheck, which is what a start script runs")
        from dynamic_config_web import check

        report = check(database)
        print(f"  would load → {report['loads']}")
        print(f"  failure    → {report['failure']}")

        drf(path)


def drf(path: Path) -> None:
    """The same surface again, through DRF's views and permissions."""
    try:
        import rest_framework  # noqa: F401
    except ImportError:
        print("\n(skipping the DRF half: pip install djangorestframework)")

        return

    from django.test import Client

    from dynamic_config_web.django.drf import urls as drf_urls

    show("the same routes, as DRF APIViews")
    # Readiness and metrics stay open — a probe is not an authenticated
    # caller — and the diagnostics sit behind `ConfigDiagnosticsPermission`,
    # which defers to the installation's guard.
    urlpatterns[:] = drf_urls()

    from django.urls import clear_url_caches

    clear_url_caches()

    path.write_text('[db]\nhost = "db.replica"\nport = 6543\n')

    client = Client()

    print(f"  GET /healthz → {client.get('/healthz').status_code}")
    print(f"  GET /readyz  → {client.get('/readyz').status_code}")
    print(f"  no token     → {client.get('/_config/check').status_code}")

    allowed = client.get("/_config/check", headers={"x-config-token": "s3cret"})
    print(f"  with one     → {allowed.status_code} {list(allowed.json())}")


if __name__ == "__main__":
    main()
