"""What the examples have in common: a model, a file, and a way to print.

Each example is a script you can run — `python examples/01_fastapi.py` —
and none of them needs a server, a network or a setup step. They write
their own configuration into a temporary directory, so a checkout stays
clean.

The model is a plain `dataclasses.dataclass`: the base wheel needs no
schema library, and an example that reached for Pydantic would quietly
make it a requirement.
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Pool:
    """A nested section, so the examples have a dotted path to explain."""

    max_size: int = 8
    timeout_seconds: float = 5.0


@dataclass
class Database:
    """The section every example configures."""

    host: str = "localhost"
    port: int = 5432
    pool: Pool = field(default_factory=Pool)


CONFIG = """
[db]
host = "db.internal"
port = 6543

[db.pool]
max_size = 16
"""


@contextmanager
def workspace(document: str = CONFIG, name: str = "config.toml") -> Iterator[Path]:
    """A temporary directory holding a configuration file."""
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / name
        path.write_text(document)

        yield path


def show(title: str) -> None:
    """A heading, so the output of a long example is readable."""
    print(f"\n{title}\n{'─' * len(title)}")
