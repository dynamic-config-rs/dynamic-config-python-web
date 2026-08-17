"""The model and the writer every test here shares.

A module rather than fixtures, because the adapters' drivers build
applications outside a fixture's scope and need the same two things.

A plain `dataclasses.dataclass` is the schema: the base wheel needs no
schema library, this suite runs in the environment that proves it, and
the engine validates a dataclass structurally — which is what makes the
"a bad port is refused" tests below real.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class Database:
    """The section these tests configure."""

    host: str = "localhost"
    port: int = 5432
    pool_size: int = 8


def write(
    path: Path, *, host: str = "db.internal", port: int = 5432, pool: int = 8
) -> None:
    """The document these tests load, in one line."""
    path.write_text(f'[db]\nhost = "{host}"\nport = {port}\npool_size = {pool}\n')
