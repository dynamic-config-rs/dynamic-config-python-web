"""What this package raises, and why each one is its own type.

Two failures, and neither is the engine's: a framework that is not
installed, and a read that happened outside the request it belongs to.
Everything else — a file that will not parse, a value the schema refuses —
is `dynamic_config`'s and arrives unchanged, because an adapter that
re-wrapped the engine's errors would make `except InvalidError` stop
working for the person who read the engine's documentation.
"""

from __future__ import annotations

__all__ = ["MissingFrameworkError", "OutsideRequestScopeError"]


class MissingFrameworkError(ImportError):
    """A framework an adapter needs is not installed.

    An `ImportError` subclass on purpose: `except ImportError` is what a
    program that probes for an optional integration already writes, and
    this must not slip past it. What it adds is the sentence that ends the
    search — which extra to install — rather than a traceback ending in
    `No module named 'litestar'`.
    """

    def __init__(self, framework: str, extra: str) -> None:
        """Names the framework and the extra that installs it."""
        super().__init__(
            f"{framework} is not installed, and "
            f"dynamic_config_web.{extra} is the adapter for it. "
            f"Install it with `pip install dynamic-config-py[{extra}]` "
            f"(or `pip install dynamic-config-py-web[{extra}]`)."
        )
        self.framework = framework
        self.extra = extra


class OutsideRequestScopeError(RuntimeError):
    """A request-scoped read happened where there is no request.

    The rule this package exists to enforce is *read the configuration
    once per request and use that value for the whole request*: a reload
    landing mid-request would otherwise show one request two
    configurations. `current()` reads from the snapshot the request began
    with, so calling it outside a request has no snapshot to read — and
    answering with "whatever is installed right now" would quietly be the
    bug the rule prevents.

    Code that is genuinely not serving a request — a startup task, a
    management command, a background consumer — wants
    :func:`~dynamic_config_web.latest`, which says so.
    """
