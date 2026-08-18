"""Django, against the shared contract.

Django's shape is the odd one here: no application object, no lifespan, and
a settings module that is loaded once. So the driver does what an installed
app's `ready()` would do — `configure()` then `start()` — and undoes it
afterwards, which a server process would instead do by exiting.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable

import pytest

pytest.importorskip("django")

from django.http import JsonResponse
from django.test import Client as DjangoClient
from django.urls import path

import django_bootstrap
import dynamic_config_web.django as dj
from dynamic_config_web import Wiring, current
from dynamic_config_web.django.urls import urls as config_urls
from helpers import Database

from . import suite

django_bootstrap.bootstrap()

_FILES: dict[str, str] = {}


class Answer:
    """One framework's response, in the shape the suite asks for."""

    def __init__(self, response: Any) -> None:
        self._response = response
        self.status_code = response.status_code

    def json(self) -> Any:
        return self._response.json()

    @property
    def text(self) -> str:
        return self._response.content.decode()


class Client:
    """Django's test client, answering `Answer`s."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    def get(self, path: str, headers: dict[str, str] | None = None) -> Answer:
        return Answer(self._inner.get(path, headers=headers or {}))


def probe(request: Any) -> JsonResponse:
    """Reads through the request, which is the Django-shaped read."""
    db: Database = request.dynamic_config.one()

    return JsonResponse({"host": db.host, "port": db.port, "pool": db.pool_size})


def tear(request: Any) -> JsonResponse:
    """Reads, reloads underneath itself, and reads again."""
    del request

    config = dj.wiring().configs[0]
    first = current(config)

    Path(_FILES["db"]).write_text(
        '[db]\nhost = "reloaded-mid-request"\nport = 5432\npool_size = 8\n'
    )
    config.reload()

    second = current(config)

    return JsonResponse(
        {
            "first": first.host,
            "second": second.host,
            "same_object": first is second,
        }
    )


def pair(request: Any) -> JsonResponse:
    """Every configuration in the wiring, pinned by one scope."""
    del request

    wiring = dj.wiring()
    first = {c.key: current(c).host for c in wiring.configs}

    for member in wiring.configs:
        if member.key != "db":
            member.set_override("host", "moved.internal")
        member.reload()

    second = {c.key: current(c).host for c in wiring.configs}

    return JsonResponse(
        {
            "db_first": first["db"],
            "db_second": second["db"],
            "extra_first": first.get("extra"),
            "extra_second": second.get("extra"),
        }
    )


class DjangoDriver:
    """What the shared suite needs to know about Django."""

    name = "django"
    scope_is_automatic = True

    #: The DRF suite is the same driver with a different route builder.
    routes = staticmethod(config_urls)

    @contextmanager
    def client(
        self,
        wiring: Wiring,
        *,
        guard: Any = None,
        stale_after: float | None = None,
    ) -> Iterator[Client]:
        """An installation over `wiring`, routed and started."""
        install = dj.configure(wiring, guard=guard, stale_after=stale_after)

        django_bootstrap.route(
            [
                *type(self).routes(install),
                path("probe", probe),
                path("tear", tear),
                path("pair", pair),
            ]
        )

        # What `AppConfig.ready()` does in a real project.
        dj.start()

        try:
            yield Client(DjangoClient())
        finally:
            # And what the process exiting does. A server never calls this;
            # a test suite that builds twelve installations in one process
            # has to.
            dj.reset()


@pytest.fixture
def _record(config_file: Path) -> Iterator[None]:
    _FILES["db"] = str(config_file)

    yield

    _FILES.clear()
    dj.reset()


@pytest.mark.usefixtures("_record")
@pytest.mark.parametrize("case", suite.cases(DjangoDriver()), ids=suite.name_of)
def test_django_conformance(
    case: Callable[..., None],
    config_file: Path,
    dynamic_config_wiring: Callable[..., Wiring],
    config: Any,
) -> None:
    """Every case in the shared contract, against Django."""
    wiring = dynamic_config_wiring(
        config, watch=suite.needs_watching(case), start=False
    )

    case(DjangoDriver(), wiring, config_file)
