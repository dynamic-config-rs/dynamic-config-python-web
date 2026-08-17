"""The request scope, as a Django middleware.

    MIDDLEWARE = [
        "dynamic_config_web.django.middleware.DynamicConfigMiddleware",
        ...,
    ]

**Put it first, or close to it.** Every later middleware and every view then
reads the same snapshot; one listed above it reads outside the scope and
raises, which is a clearer failure than reading a different generation.

Sync and async in one class, because a Django project may serve both and
`MIDDLEWARE` is one list. Django asks the class which it supports and adapts
the chain around it, so declaring both here is what keeps it from wrapping
the middleware in `sync_to_async` — a wrapper that would run the view in a
different thread and, worse, could leave the scope open on the wrong one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

from asgiref.sync import iscoroutinefunction, markcoroutinefunction

from dynamic_config_web._scope import enter, leave

from . import RequestConfig, installation

if TYPE_CHECKING:  # pragma: no cover - typing only
    from django.http import HttpRequest

__all__ = ["DynamicConfigMiddleware"]


class DynamicConfigMiddleware:
    """Opens one request scope per request, and attaches it to the request."""

    sync_capable = True
    async_capable = True

    def __init__(self, get_response: Callable[..., Any]) -> None:
        """Django builds one per handler, and tells it which it is."""
        self.get_response = get_response
        self._async = iscoroutinefunction(get_response)

        if self._async:
            markcoroutinefunction(self)

    def __call__(self, request: HttpRequest) -> Any:
        """The sync path, and the dispatch to the async one."""
        if self._async:
            return self.__acall__(request)

        install = installation()
        token = enter(install.wiring.configs)
        request.dynamic_config = RequestConfig(install.wiring)

        try:
            return self.get_response(request)
        finally:
            # In a `finally` rather than after the response, because a view
            # that raises still has to leave the context as it found it.
            leave(token)

    async def __acall__(self, request: HttpRequest) -> Any:
        """The async path. One scope, one task, one snapshot."""
        install = installation()
        token = enter(install.wiring.configs)
        request.dynamic_config = RequestConfig(install.wiring)

        try:
            return await self.get_response(request)
        finally:
            leave(token)
