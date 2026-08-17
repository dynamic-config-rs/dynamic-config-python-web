r"""The pytest plugin: a wiring that stops itself, and a way to pin values.

Loaded automatically — the distribution declares a `pytest11` entry
point, so installing it is all a suite has to do. Nothing here is autouse:
a plugin that arrives with a package must not change what a test sees
until the test asks for it.

    def test_the_service_reads(dynamic_config_workspace, dynamic_config_wiring):
        path = dynamic_config_workspace / "app.toml"
        path.write_text("[db]\nport = 5432\n")

        config = DynamicConfig(Database, key="db").file("app.toml")
        wiring = dynamic_config_wiring(config)

        with TestClient(build(wiring)) as client:
            assert client.get("/health").json()["port"] == 5432

The base wheel's plugin ships `dynamic_config_workspace` and
`dynamic_config_env`, and both load alongside these: two entry points,
one session, no import between them.

This module imports pytest, the standard library and this package's
framework-free half. It imports **no framework** — a plugin that pulled in
FastAPI would make `pip install dynamic-config-py-web` slower for the
Django user and heavier for everyone.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING, Any, Callable

import pytest

from ._lease import holders
from ._testing import as_request, pinned
from ._wiring import Wiring

if TYPE_CHECKING:  # pragma: no cover - typing only
    from dynamic_config import ConfigGroup, DynamicConfig


@pytest.fixture
def dynamic_config_wiring() -> Iterator[Callable[..., Wiring]]:
    """A factory for wirings that are stopped when the test ends.

        wiring = dynamic_config_wiring(config, watch=False)

    Stopping is the part worth automating: a test that leaves a watcher
    running leaves the *next* test a configuration that cannot be watched
    — `AlreadyExists` — and the failure lands two tests away from its
    cause.

    `watch=False` is the sensible default for a test that reloads by
    hand; pass `watch=True` for the tests that are about watching.
    """
    made: list[Wiring] = []

    def build(
        target: DynamicConfig[Any] | ConfigGroup,
        *,
        watch: bool = False,
        start: bool = True,
        **options: Any,
    ) -> Wiring:
        wiring = Wiring(target, watch=watch, **options)
        made.append(wiring)

        if start:
            wiring.start()

        return wiring

    yield build

    for wiring in reversed(made):
        wiring.stop()


@pytest.fixture
def dynamic_config_pinned() -> Callable[..., Any]:
    """The engine's `overrides` block, under the name a web test wants.

        with dynamic_config_pinned(config, pool__max_size=1):
            assert client.get("/health").json()["pool"] == 1

    Dotted paths are spelled with a double underscore, because a keyword
    argument cannot carry a dot.
    """
    return pinned


@pytest.fixture
def dynamic_config_request() -> Callable[..., Any]:
    """A request scope, for a test that calls a handler without a request.

        with dynamic_config_request(config):
            assert view() == "db.internal"

    An adapter opens one per request; a unit test calling the function
    directly has no adapter in the way, and the scoped read would
    otherwise raise.
    """
    return as_request


@pytest.fixture
def dynamic_config_watchers() -> Callable[[Any], int]:
    """How many wirings hold a configuration's watcher, for the tests.

    The one question a lifetime test has to be able to ask: *did that
    block leave anything running?*
    """
    return holders
