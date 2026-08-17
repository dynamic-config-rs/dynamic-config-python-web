"""Two doors for a test, and neither of them writes a file.

A test that wants one value different should not have to build a
configuration file, and a test that calls a handler directly should not
have to stand up a request to satisfy the request scope. Those are the two
things this package makes harder than the raw engine does, so it is this
package's job to make them easy again.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

from . import _scope

if TYPE_CHECKING:  # pragma: no cover - typing only
    from dynamic_config import ConfigGroup, DynamicConfig

__all__ = ["as_request", "pinned"]


@contextmanager
def pinned(config: DynamicConfig[Any], **values: Any) -> Iterator[None]:
    """Pins values for the block, and puts back what it found.

        with pinned(config, pool__max_size=1):
            assert client.get("/health").json()["pool"] == 1

    A thin wrapper over the engine's own `overrides`, and thin on
    purpose — what it adds is the name: in a web test the interesting
    thing is that the *service* now sees the value, and `pinned` reads
    that way at the call site.

    Dotted paths are spelled with a double underscore
    (`pool__max_size` → `pool.max_size`), because a keyword argument
    cannot carry a dot. The block reloads on the way in and on the way
    out, so it touches the sources: it belongs in a test, not in a
    handler.
    """
    with config.overrides(**values):
        yield


@contextmanager
def as_request(*targets: DynamicConfig[Any] | ConfigGroup) -> Iterator[None]:
    """Opens a request scope by hand, for a test that has no request.

        with as_request(config):
            assert handler() == "db.internal"

    An adapter opens one per request; a unit test calling a handler
    directly has no adapter in the way, and `current()` would raise. This
    is the same scope, entered deliberately — which also makes it the
    tool for a background task that wants one configuration for the
    length of a job.
    """
    with _scope.scope(*targets):
        yield
