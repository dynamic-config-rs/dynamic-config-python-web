"""What every test here has: a model, a file, and a wiring that stops itself.

The base wheel's own plugin supplies `dynamic_config_workspace` (a scratch
directory that is also the working directory) and `dynamic_config_env` (an
environment nobody else's shell reaches into); this package's supplies
`dynamic_config_wiring` and friends. Both arrive through entry points, so
there is no import here to keep in step.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import pytest

from dynamic_config import DynamicConfig
from dynamic_config_web import Wiring
from helpers import Database, write


@pytest.fixture
def workspace(dynamic_config_workspace: Path) -> Path:
    """A scratch directory that is also the working directory."""
    return dynamic_config_workspace


@pytest.fixture(autouse=True)
def _clean_environment(dynamic_config_env: Callable[..., None]) -> None:
    """No test inherits another's variables."""
    dynamic_config_env("APP_", "WEBTEST_")


@pytest.fixture
def config_file(workspace: Path) -> Path:
    """A configuration file with a document already in it."""
    path = workspace / "config.toml"
    write(path)

    return path


@pytest.fixture
def config(config_file: Path) -> DynamicConfig[Database]:
    """One configuration over that file, not yet loaded."""
    return DynamicConfig(Database, key="db").file(str(config_file))


@pytest.fixture
def wiring(
    config: DynamicConfig[Database], dynamic_config_wiring: Callable[..., Wiring]
) -> Wiring:
    """A started wiring over that configuration, stopped when the test ends."""
    return dynamic_config_wiring(config, watch=False)
