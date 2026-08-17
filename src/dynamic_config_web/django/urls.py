"""The routes, as a URLconf to include.

    urlpatterns = [
        path("", include("dynamic_config_web.django.urls")),
        ...,
    ]

That gives `/healthz`, `/readyz` and — unless `metrics` is off — `/metrics`,
plus the two diagnostics routes when and only when the installation carries a
guard. Under a prefix::

    path("internal/", include("dynamic_config_web.django.urls")),

For a project that would rather be explicit, :func:`urls` builds the same
list and takes the installation as an argument, which is also what a test
does when it needs two different guards in one process.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Optional

from django.urls import path, re_path

from . import Installation, installation
from .views import (
    check_view,
    diagnostics_are_available,
    explain_view,
    healthz,
    metrics,
    readyz,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from django.urls import URLPattern

__all__ = ["urlpatterns", "urls"]


def urls(install: Optional[Installation] = None) -> list[URLPattern]:
    """Every route this installation should serve."""
    install = install or installation()

    routes: list[URLPattern] = [
        path("healthz", healthz, name="dynamic-config-healthz"),
        path("readyz", readyz, name="dynamic-config-readyz"),
    ]

    if install.metrics:
        routes.append(path("metrics", metrics, name="dynamic-config-metrics"))

    if diagnostics_are_available(install):
        prefix = install.diagnostics_prefix

        routes.extend(
            [
                # `re_path` with a greedy tail, because a dotted path is what
                # `explain` takes and Django's `<path:…>` converter would
                # also match the query-string-free rest of the URL — this
                # says the same thing in the spelling that cannot surprise.
                re_path(
                    rf"^{prefix}/explain/(?P<path>.+)$",
                    explain_view,
                    name="dynamic-config-explain",
                ),
                path(
                    f"{prefix}/check",
                    check_view,
                    name="dynamic-config-check",
                ),
            ]
        )

    return routes


class _LazyPatterns(Sequence):  # type: ignore[type-arg]
    """`urlpatterns`, built when Django first resolves rather than at import.

    Importing a URLconf is not the same moment as serving from it — a
    project may import this module from `urls.py` before `ready()` has run,
    and a test rewires the installation between clients. Django asks a
    URLconf module for `urlpatterns` and iterates it once per resolver, so
    building here is both late enough to be correct and cached by Django's
    own `clear_url_caches()` machinery.
    """

    def __iter__(self) -> Any:
        return iter(urls())

    def __len__(self) -> int:
        return len(urls())

    def __getitem__(self, index: Any) -> Any:
        return urls()[index]

    def __repr__(self) -> str:
        return "<dynamic-config urlpatterns>"


#: What `include("dynamic_config_web.django.urls")` picks up.
urlpatterns = _LazyPatterns()
