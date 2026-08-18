"""The reload log lines and the event stream — `_events.py`, from outside.

The module had shipped without a test of its own: `log_reloads` was read
but never asserted, and `stream_events` was exercised only by an example.
These pin the two things a consumer relies on: what a line *says* (paths,
never values) and what an event *carries*.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import pytest

from dynamic_config import ConfigGroup, DynamicConfig
from dynamic_config_web import log_reloads, stream_events
from helpers import Database, write


def test_log_reloads_names_the_paths_that_moved(
    wiring, config_file: Path, caplog: pytest.LogCaptureFixture
) -> None:
    config = wiring.configs[0]

    guards = log_reloads(config)

    try:
        with caplog.at_level(logging.INFO, logger="dynamic_config"):
            write(config_file, host="moved", port=5433)
            config.reload()

        assert len(caplog.records) == 1
        line = caplog.records[0].getMessage()

        assert "db" in line
        assert "host" in line
        assert "port" in line
    finally:
        for guard in guards:
            guard.close()


def test_log_reloads_carries_no_value(
    wiring, config_file: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Paths, never values — the module's own first rule."""
    config = wiring.configs[0]

    guards = log_reloads(config)

    try:
        with caplog.at_level(logging.INFO, logger="dynamic_config"):
            write(config_file, host="hunter2.internal", port=4242)
            config.reload()

        text = "\n".join(record.getMessage() for record in caplog.records)

        assert "hunter2" not in text
        assert "4242" not in text
    finally:
        for guard in guards:
            guard.close()


def test_log_reloads_takes_a_logger_and_a_level(
    wiring, config_file: Path, caplog: pytest.LogCaptureFixture
) -> None:
    config = wiring.configs[0]
    mine = logging.getLogger("test.reloads")

    guards = log_reloads(config, mine, level=logging.WARNING)

    try:
        with caplog.at_level(logging.WARNING, logger="test.reloads"):
            write(config_file, host="elsewhere")
            config.reload()

        assert caplog.records, "the line went to the wrong logger"
        assert caplog.records[0].levelno == logging.WARNING
        assert caplog.records[0].name == "test.reloads"
    finally:
        for guard in guards:
            guard.close()


def test_log_reloads_covers_every_member_of_a_group(
    tmp_path: Path, dynamic_config_wiring, caplog: pytest.LogCaptureFixture
) -> None:
    first_file = tmp_path / "first.toml"
    second_file = tmp_path / "second.toml"
    first_file.write_text('[db]\nhost = "a"\nport = 1\npool_size = 1\n')
    second_file.write_text('[extra]\nhost = "b"\nport = 2\npool_size = 2\n')

    group = ConfigGroup(
        DynamicConfig(Database, key="db").file(str(first_file)),
        DynamicConfig(Database, key="extra").file(str(second_file)),
    )
    wiring = dynamic_config_wiring(group, watch=False)

    guards = log_reloads(group)

    try:
        assert len(guards) == 2, "one hook per member"

        with caplog.at_level(logging.INFO, logger="dynamic_config"):
            second_file.write_text('[extra]\nhost = "c"\nport = 2\npool_size = 2\n')
            wiring.configs[1].reload()

        lines = [record.getMessage() for record in caplog.records]

        assert any("extra" in line for line in lines)
        assert not any('"c"' in line for line in lines)
    finally:
        for guard in guards:
            guard.close()


def test_stream_events_reports_an_install(wiring, config_file: Path) -> None:
    config = wiring.configs[0]

    async def one_event() -> dict:
        stream = stream_events(config, failure_poll=None)

        async def consume() -> dict:
            async for event in stream:
                return event
            raise AssertionError("the stream ended")

        task = asyncio.ensure_future(consume())

        # Let the consumer subscribe before the install lands.
        await asyncio.sleep(0.05)
        write(config_file, host="streamed")
        config.reload()

        return await asyncio.wait_for(task, timeout=5)

    event = asyncio.run(one_event())

    assert event["type"] == "reloaded"
    assert event["key"] == "db"
    assert event["generation"] == 2
    assert "host" in event["changed"]
    assert "streamed" not in str(event), "an event carried a value"


def test_stream_events_reports_a_refusal(wiring, config_file: Path) -> None:
    """A load that installed nothing still reaches the stream.

    Nothing bumps a generation on a refusal, so `failure_poll` is the
    only wake-up — this is the path that exists for it.
    """
    config = wiring.configs[0]

    async def first_failure() -> dict:
        stream = stream_events(config, failure_poll=0.05)

        async def consume() -> dict:
            async for event in stream:
                if event["type"] == "reload_failed":
                    return event
            raise AssertionError("the stream ended")

        task = asyncio.ensure_future(consume())

        await asyncio.sleep(0.05)
        config_file.write_text("this is not toml [")

        import contextlib

        with contextlib.suppress(Exception):
            config.reload()  # the refusal is the point

        return await asyncio.wait_for(task, timeout=5)

    event = asyncio.run(first_failure())

    assert event["type"] == "reload_failed"
    assert event["key"] == "db"
    assert event["kind"] == "parse"
    assert event["consecutive"] >= 1
