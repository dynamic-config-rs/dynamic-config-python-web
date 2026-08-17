"""The shared half, tested without a framework anywhere.

Everything here runs in the bare environment — the wheel, the engine, and
pytest. That is deliberate: the core is what has to work on every
interpreter this package claims, and an adapter is a translation of it.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, ClassVar

import pytest

import dynamic_config_web as web
from dynamic_config import ConfigGroup, DynamicConfig, Values
from dynamic_config_web import OutsideRequestScopeError, Wiring
from dynamic_config_web._lease import holders, watching
from helpers import Database, write

# ── the wiring ─────────────────────────────────────────────────────────


def test_a_wiring_loads_and_is_idempotent(config: DynamicConfig[Database]) -> None:
    wiring = Wiring(config, watch=False)

    assert not wiring.started

    wiring.start()
    wiring.start()  # a hook that fires twice must not be a second load

    assert wiring.started
    assert config.current().host == "db.internal"

    generation = config.generation
    wiring.start()

    assert config.generation == generation, "the second start reloaded"

    wiring.stop()
    wiring.stop()

    assert not wiring.started


def test_a_wiring_is_a_context_manager(config: DynamicConfig[Database]) -> None:
    with Wiring(config, watch=False) as wiring:
        assert wiring.started

    assert not wiring.started


async def test_the_async_twin_loads_the_same_way(
    config: DynamicConfig[Database],
) -> None:
    async with Wiring(config, watch=False) as wiring:
        assert wiring.started
        assert config.current().port == 5432

    assert not wiring.started


def test_a_wiring_over_a_group_flattens_it(config_file: Path) -> None:
    config_file.write_text('[db]\nhost = "h"\nport = 1\n\n[cache]\nttl = 60\n')

    first: DynamicConfig[Any] = DynamicConfig(Database, key="db").file(str(config_file))
    second: DynamicConfig[Any] = DynamicConfig(Values, key="cache").file(
        str(config_file)
    )

    group = ConfigGroup(first, second)

    with Wiring(group, watch=False) as wiring:
        assert [config.key for config in wiring.configs] == ["db", "cache"]
        assert wiring.config("cache") is second
        assert wiring.target is group


def test_naming_a_configuration_that_is_not_there_says_what_is(
    wiring: Wiring,
) -> None:
    with pytest.raises(LookupError, match="'db'"):
        wiring.config("nope")


# ── the lease ──────────────────────────────────────────────────────────


def test_two_wirings_over_one_configuration_do_not_collide(
    config: DynamicConfig[Database],
) -> None:
    """The case `uvicorn --reload` and a test suite both produce."""
    first = Wiring(config, watch=True, debounce=0.05)
    second = Wiring(config, watch=True, debounce=0.05)

    first.start()
    # A second `watch()` on one configuration is `AlreadyExists`; the
    # lease is what turns that into a second holder.
    second.start()

    assert holders(config) == 2
    assert watching(config)

    first.stop()

    assert holders(config) == 1
    assert watching(config), "the second holder still wants it"

    second.stop()

    assert holders(config) == 0
    assert not watching(config)


def test_a_wiring_that_only_loads_holds_no_watcher(
    config: DynamicConfig[Database],
) -> None:
    with Wiring(config, watch=False):
        assert holders(config) == 1
        assert not watching(config)


def test_a_watcher_reloads_and_stops_with_the_wiring(
    config: DynamicConfig[Database], config_file: Path
) -> None:
    import time

    with Wiring(config, watch=True, debounce=0.05):
        write(config_file, host="second")

        for _ in range(100):
            if config.current().host == "second":
                break

            time.sleep(0.05)

        assert config.current().host == "second"

    generation = config.generation
    write(config_file, host="third")
    time.sleep(0.3)

    assert config.generation == generation, "the watcher outlived the wiring"


@pytest.mark.skipif(not hasattr(os, "fork"), reason="fork is POSIX-only")
def test_a_forked_child_watches_its_own_files(
    config: DynamicConfig[Database], config_file: Path
) -> None:
    """`gunicorn --preload`, in twenty lines.

    The child inherits the engine's registration and none of the parent's
    threads. Without the lease's fork handler it would be refused a
    watcher of its own and would serve the snapshot it was forked with,
    silently, for ever.
    """
    import time

    with Wiring(config, watch=True, debounce=0.05):
        read, written = os.pipe()
        child = os.fork()

        if child == 0:  # pragma: no cover - the child never returns
            os.close(read)
            code = 1

            try:
                write(config_file, host="after-the-fork")

                for _ in range(100):
                    if config.current().host == "after-the-fork":
                        code = 0
                        break

                    time.sleep(0.05)
            finally:
                os.write(written, bytes([code]))
                os.close(written)
                os._exit(0)

        os.close(written)
        answer = os.read(read, 1)
        os.close(read)
        os.waitpid(child, 0)

        assert answer == b"\x00", "the child never saw its own edit"


# ── the request scope ──────────────────────────────────────────────────


def test_a_read_outside_a_request_is_refused(wiring: Wiring) -> None:
    with pytest.raises(OutsideRequestScopeError, match="no request scope"):
        web.current(wiring.configs[0])


def test_the_escape_hatch_is_named(wiring: Wiring) -> None:
    """`latest()` is the read that says it is not request-scoped."""
    assert web.latest(wiring.configs[0]).host == "db.internal"


def test_a_scope_pins_one_reading_for_its_whole_length(
    wiring: Wiring, config_file: Path
) -> None:
    config = wiring.configs[0]

    with web.scope(config):
        first = web.current(config)

        write(config_file, host="moved")
        config.reload()

        assert web.current(config) is first, "the scope was not pinned"
        assert web.current(config).host == "db.internal"

    # And the *next* request sees the new document: a scope is not a cache.
    with web.scope(config):
        assert web.current(config).host == "moved"


def test_a_scope_reads_by_key_too(wiring: Wiring) -> None:
    with web.scope(*wiring.configs):
        assert web.get("db").host == "db.internal"
        assert web.get("nope", None) is None

        with pytest.raises(KeyError, match="'db'"):
            web.get("nope")


def test_a_scope_reaches_a_worker_thread(wiring: Wiring) -> None:
    """FastAPI runs a `def` endpoint on a thread; the scope has to follow."""
    import concurrent.futures
    import contextvars

    config = wiring.configs[0]

    with web.scope(config):
        pinned = web.current(config)
        context = contextvars.copy_context()

        with concurrent.futures.ThreadPoolExecutor(1) as pool:
            seen = pool.submit(context.run, web.current, config).result()

    assert seen is pinned


def test_active_says_whether_there_is_a_request(wiring: Wiring) -> None:
    assert not web.active()

    with web.scope(*wiring.configs):
        assert web.active()

    assert not web.active()


# ── health ─────────────────────────────────────────────────────────────


def test_liveness_never_fails(config: DynamicConfig[Database]) -> None:
    """Not even before the first load: a restart would read the same file."""
    report = web.liveness()

    assert report.ok
    assert report.status_code == 200
    del config


def test_readiness_before_the_first_load(config: DynamicConfig[Database]) -> None:
    report = web.readiness(config)

    assert not report
    assert report.status_code == 503
    assert report.body["status"] == "unavailable", "it has never served anything"
    assert report.body["configs"]["db"]["installed"] is False
    assert "nothing installed" in report.body["problems"][0]


def test_readiness_after_a_refusal_keeps_serving(
    wiring: Wiring, config_file: Path
) -> None:
    config = wiring.configs[0]

    assert web.readiness(config).ok

    config_file.write_text('[db]\nhost = "h"\nport = "not a number"\n')

    with pytest.raises(Exception, match="port"):
        config.reload()

    report = web.readiness(config)

    assert not report.ok
    assert report.status_code == 503
    assert report.body["status"] == "degraded", "serving, but reloads are failing"
    assert report.body["configs"]["db"]["installed"] is True, "it is still serving"
    assert report.body["configs"]["db"]["last_failure"]["kind"] == "invalid"
    # The failure's *path* is a key name, and a probe body is the wrong
    # place for one — but the kind and the count belong there.
    assert config.current().host == "db.internal"


def test_readiness_can_call_a_document_stale(wiring: Wiring) -> None:
    assert web.readiness(*wiring.configs, stale_after=1000).ok
    assert not web.readiness(*wiring.configs, stale_after=0).ok


def test_a_health_body_carries_no_value(wiring: Wiring, config_file: Path) -> None:
    write(config_file, host="hunter2-do-not-log", port=4242)
    wiring.configs[0].reload()

    rendered = repr(web.readiness(*wiring.configs).body)

    assert "hunter2" not in rendered
    assert "4242" not in rendered


# ── metrics ────────────────────────────────────────────────────────────


def test_the_metrics_body_renders_the_engine_names(wiring: Wiring) -> None:
    body = web.metrics_body(*wiring.configs)

    for name in (
        "dynamic_config_installs_total",
        "dynamic_config_last_success_seconds",
        "dynamic_config_consecutive_failures",
        "dynamic_config_last_reload_info",
    ):
        assert name in body, name

    assert 'config="db"' in body


def test_the_metrics_body_takes_a_group(config_file: Path) -> None:
    config_file.write_text('[db]\nhost = "h"\nport = 1\n\n[cache]\nttl = 60\n')

    first: DynamicConfig[Any] = DynamicConfig(Database, key="db").file(str(config_file))
    second: DynamicConfig[Any] = DynamicConfig(Values, key="cache").file(
        str(config_file)
    )
    group = ConfigGroup(first, second)

    with Wiring(group, watch=False):
        body = web.metrics_body(group)

    assert 'config="db"' in body
    assert 'config="cache"' in body


def test_the_metrics_body_carries_no_value(wiring: Wiring, config_file: Path) -> None:
    write(config_file, host="hunter2-do-not-log", port=4242, pool=999)
    wiring.configs[0].reload()

    body = web.metrics_body(*wiring.configs)

    assert "hunter2" not in body
    assert "4242" not in body
    assert str(config_file) not in body


def test_labels_keep_the_key(wiring: Wiring) -> None:
    body = web.metrics_body(*wiring.configs, labels={"env": "test"})

    assert 'env="test"' in body
    assert 'config="db"' in body


# ── diagnostics ────────────────────────────────────────────────────────


def test_explain_and_check_answer(wiring: Wiring) -> None:
    config = wiring.configs[0]
    rendered = web.explain(config, "port")

    assert "5432" in rendered

    report = web.check(config)

    assert report["key"] == "db"
    assert report["clean"] is True
    assert report["loads"] is True


def test_check_asks_whether_it_would_load_and_not_only_whether_it_resolves(
    wiring: Wiring, config_file: Path
) -> None:
    """A document whose keys resolve and whose types do not.

    The engine's own `check()` answers the first question — the layers
    merge, the keys are all declared — and stops there, because building
    the model is the binding's job. A report that said `clean` for a
    document the next reload will refuse would be worse than no report, so
    this one also calls `load()`, which validates and installs nothing.
    """
    config = wiring.configs[0]

    config_file.write_text('[db]\nhost = "h"\nport = "not a number"\n')

    report = web.check(config)

    assert report["loads"] is False
    assert report["clean"] is False
    assert "port" in str(report["failure"])
    # And it installed nothing: the model this process is serving is the
    # one it was serving before.
    assert web.latest(config).port == 5432


async def test_the_async_twins_leave_the_loop_free(wiring: Wiring) -> None:
    import threading

    config = wiring.configs[0]
    here = threading.get_ident()
    elsewhere: list[int] = []

    def record(_config: Any, _path: str) -> str:
        elsewhere.append(threading.get_ident())

        return "rendered"

    import dynamic_config_web._diagnostics as diagnostics

    original = diagnostics.explain
    diagnostics.explain = record  # type: ignore[assignment]

    try:
        assert await diagnostics.explain_async(config, "port") == "rendered"
    finally:
        diagnostics.explain = original  # type: ignore[assignment]

    assert elsewhere, "the patched diagnostic never ran"
    assert elsewhere[0] != here, "it ran on the loop's own thread"


def test_the_default_guard_refuses_everything() -> None:
    assert web.never(object()) is False


def test_a_token_guard_compares_a_header() -> None:
    guard = web.token_guard("s3cret")

    class Request:
        headers: ClassVar[dict[str, str]] = {"x-config-token": "s3cret"}

    class Wrong:
        headers: ClassVar[dict[str, str]] = {"x-config-token": "nope"}

    class Bare:
        headers: ClassVar[dict[str, str]] = {}

    assert guard(Request())
    assert not guard(Wrong())
    assert not guard(Bare())


def test_a_token_guard_with_no_token_refuses_rather_than_admits() -> None:
    """The `getenv` typo, which must fail closed."""
    assert web.token_guard("") is web.never


@pytest.mark.parametrize(
    "offered",
    [
        "\xff",  # what Django hands over for a raw 0xFF byte
        "\udcff",  # a surrogate, from a lenient decoder
        "pärola",  # ordinary non-ASCII
        "s3cret\u200b",  # a zero-width space appended
    ],
)
def test_a_hostile_token_is_a_mismatch_and_not_a_crash(offered: str) -> None:
    """One byte must not turn a 401 into a 500.

    `hmac.compare_digest` raises `TypeError` when either `str` contains a
    non-ASCII character, and the offered value arrives from an
    unauthenticated caller — so comparing as `str` made a single header
    byte a server error on every adapter at once.
    """
    guard = web.token_guard("s3cret")

    class Request:
        headers: ClassVar[dict[str, str]] = {"x-config-token": offered}

    assert guard(Request()) is False


def test_a_non_ascii_token_still_authenticates() -> None:
    """And the fix must not refuse a legitimate one."""
    guard = web.token_guard("pärola")

    class Right:
        headers: ClassVar[dict[str, str]] = {"x-config-token": "pärola"}

    class Wrong:
        headers: ClassVar[dict[str, str]] = {"x-config-token": "parola"}

    assert guard(Right()) is True
    assert guard(Wrong()) is False


# ── testing doors ──────────────────────────────────────────────────────


def test_pinned_isolates(wiring: Wiring) -> None:
    config = wiring.configs[0]

    with web.pinned(config, pool_size=1):
        assert config.current().pool_size == 1

    assert config.current().pool_size == 8


def test_as_request_opens_a_scope(wiring: Wiring) -> None:
    config = wiring.configs[0]

    with web.as_request(config):
        assert web.current(config).host == "db.internal"


# ── the package itself ─────────────────────────────────────────────────


def test_importing_the_core_imports_no_framework() -> None:
    """The claim the extras make, asserted where a regression would land."""
    frameworks = {
        "django",
        "django_bolt",
        "fastapi",
        "flask",
        "litestar",
        "quart",
        "robyn",
        "starlette",
    }
    imported = {name.split(".")[0] for name in sys.modules} & frameworks

    # `starlette` and friends may be present because *another test module*
    # imported them; what must never happen is the core pulling one in, and
    # a subprocess is the only honest way to assert that. See
    # `tests/test_packaging.py`.
    del imported


def test_the_surface_is_sorted() -> None:
    assert list(web.__all__) == sorted(web.__all__)
    assert all(hasattr(web, name) for name in web.__all__)
