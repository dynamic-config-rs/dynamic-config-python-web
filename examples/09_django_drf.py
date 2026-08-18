"""Django REST Framework: the health surface inside DRF's own auth.

    pip install django djangorestframework
    python examples/09_django_drf.py

The Django adapter supplies everything that is not routing — `AppConfig.ready`
loads and watches, the middleware opens the request scope — and the plain
Django views would already work in a DRF project. What this module adds is
the *permission seam*: the diagnostics sit behind
`ConfigDiagnosticsPermission`, which defers to the installation's guard and
can be swapped for `IsAdminUser`, a scope check, or anything else DRF
offers.

A real project writes:

    INSTALLED_APPS = [..., "dynamic_config_web.django", "rest_framework"]
    MIDDLEWARE = ["dynamic_config_web.django.middleware.DynamicConfigMiddleware", ...]
    DYNAMIC_CONFIG = {"target": "myproject.config:database", "guard": "..."}

    from dynamic_config_web.django.drf import urls as config_urls
    urlpatterns = [path("internal/", include(config_urls())), ...]

One convention to know: a request DRF refuses gets **403**, where the plain
views answer 404 and django-ninja answers 401. Each adapter keeps its
framework's own convention.
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

try:
    import rest_framework  # noqa: F401
except ImportError:  # pragma: no cover
    raise SystemExit(
        "this example needs DRF: pip install djangorestframework"
    ) from None

#: Rewritten in `main()`, once the routes exist.
urlpatterns: list[Any] = []

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
        INSTALLED_APPS=["dynamic_config_web.django", "rest_framework"],
        MIDDLEWARE=["dynamic_config_web.django.middleware.DynamicConfigMiddleware"],
        DATABASES={},
        USE_TZ=True,
        REST_FRAMEWORK={"UNAUTHENTICATED_USER": None},
        DYNAMIC_CONFIG={
            "target": f"{__name__}:database",
            "debounce": 0.05,
            "guard": f"{__name__}:guard",
        },
    )


def main() -> None:
    """Runs the DRF example end to end."""
    global database

    with workspace() as path:
        database = DynamicConfig(Database, key="db").file(str(path)).env("APP_")

        configure()
        django.setup()

        from django.test import Client
        from rest_framework.decorators import api_view
        from rest_framework.response import Response

        from dynamic_config_web.django import snapshot
        from dynamic_config_web.django.drf import urls as config_urls

        @api_view(["GET"])
        def index(request: Any) -> Response:
            """`snapshot()` needs no argument — the middleware scoped it."""
            del request

            db: Database = snapshot()

            return Response(
                {"host": db.host, "port": db.port, "pool": db.pool.max_size}
            )

        from django.urls import path as route

        urlpatterns[:] = [route("", index), *config_urls()]

        client = Client()

        show("serving")
        print(f"  GET /        → {client.get('/').json()}")

        show("the health surface, as APIViews")
        print(f"  GET /healthz → {client.get('/healthz').status_code}")

        ready = client.get("/readyz")
        print(f"  GET /readyz  → {ready.status_code} {ready.json()['status']}")
        print(f"  GET /metrics → {client.get('/metrics').status_code}")

        show("a deployment edits the file")
        path.write_text('[db]\nhost = "db.replica"\nport = 6543\n')
        database.reload()

        print(f"  GET /        → {client.get('/').json()}")

        show("diagnostics, behind DRF's permission")
        # 403 rather than ninja's 401 or the plain views' 404: DRF's
        # convention, kept on purpose.
        print(f"  no token  → {client.get('/_config/check').status_code}")

        answer = client.get(
            "/_config/explain/port", headers={"x-config-token": "s3cret"}
        )
        print(f"  with one  → {answer.status_code}")

        for line in answer.content.decode().splitlines()[:4]:
            print(f"    {line}")


if __name__ == "__main__":
    main()
