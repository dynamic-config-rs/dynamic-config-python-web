"""The raw-ASGI request scope, shared by every ASGI adapter.

FastAPI and Litestar carried the same fifteen lines each; this is that
middleware once. Raw ASGI rather than either framework's middleware
class, because a scope opened around `receive`/`send` is opened around
*everything* — routing included — and depends on nothing the frameworks
disagree about.

Only `http` gets a scope. A `websocket` scope IS the connection, and a
connection that lives an hour pinned to the configuration it opened with
would be the opposite of what this package is for — `latest()` is the
documented read there (the book's Limitations page owns that decision).
`lifespan` and anything else pass through untouched.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from . import _scope

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ._wiring import Wiring

__all__ = ["ScopeMiddleware"]


class ScopeMiddleware:
    """One reading per request, entered before routing, left after send."""

    def __init__(self, app: Any, wiring: Wiring) -> None:
        self._app = app
        self._wiring = wiring

    # `Any` throughout the ASGI triple, deliberately: one framework types
    # `scope` as a TypedDict union, another as a MutableMapping, and this
    # middleware must satisfy both protocols structurally.
    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        # `str()` on purpose: one framework types `scope["type"]` as a
        # Literal union, and comparing through `str` satisfies both it
        # and the plain-dict world without a cast.
        if str(scope.get("type")) != "http":
            await self._app(scope, receive, send)

            return

        token = _scope.enter(self._wiring.configs)

        try:
            await self._app(scope, receive, send)
        finally:
            _scope.leave(token)
