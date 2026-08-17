"""The Prometheus body, for whatever `/metrics` route a service already has.

`dynamic_config.Exposition` renders the twelve series; this is the two
things a web application needs around it and the engine deliberately does
not choose: a group is a group (`Exposition` takes configurations one at a
time), and a body needs a content type.

The metric names are the engine's API — they end up in dashboards and
alert rules — so nothing here renames, adds to or filters them.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any

from dynamic_config import Exposition

if TYPE_CHECKING:  # pragma: no cover - typing only
    from dynamic_config import ConfigGroup, DynamicConfig

__all__ = ["CONTENT_TYPE", "metrics_body"]

#: What a Prometheus scrape expects, and what every adapter's route sets.
#: The version is the exposition format's, not this package's.
CONTENT_TYPE = "text/plain; version=0.0.4"


def metrics_body(
    *targets: DynamicConfig[Any] | ConfigGroup,
    remote: bool = True,
    labels: Mapping[str, str] | None = None,
) -> str:
    """Every configuration in `targets`, as a Prometheus exposition body.

        body = metrics_body(group)

    Built per scrape and thrown away — the numbers are read from atomic
    counters, so this is cheap enough for a `/metrics` handler and needs
    no cache in front of it.

    Parameters:
        targets: configurations, groups, or a mix. A group contributes
            each of its members under that member's own key, which is
            what makes `metrics_body(group)` and
            `metrics_body(db, cache)` render the same thing.
        remote: also render the remote-store series for configurations
            that have a store. Harmless when none does — a configuration
            with no store adds nothing — and off for a service that would
            rather not carry six more series per configuration.
        labels: labels for every series, in place of the default
            `config="{key}"`. The key is added under `config` unless the
            mapping already names it, so a caller adding `env="prod"`
            does not lose which configuration a series is about.
    """
    exposition = Exposition()

    for config in _members(targets):
        if labels is None:
            exposition.add(config.key, config)

            if remote:
                exposition.add_remote(config.key, config)

            continue

        together = {"config": config.key, **labels}
        exposition.add_with(together, config)

        if remote:
            exposition.add_remote_with(together, config)

    return exposition.render()


def _members(
    targets: Iterable[DynamicConfig[Any] | ConfigGroup],
) -> Iterable[DynamicConfig[Any]]:
    """Every configuration in `targets`, groups flattened."""
    for target in targets:
        members = getattr(target, "configs", None)

        if members is None:
            yield target  # type: ignore[misc]
        else:
            yield from members
