"""The installed app: where a Django process loads and starts watching.

    INSTALLED_APPS = [..., "dynamic_config_web.django"]

`AppConfig.ready()` is the one hook Django offers that runs after settings
are loaded, after the app registry is populated, and before anything is
served — which is exactly the moment configuration has to exist. It is also
called in every process Django starts, including the ones that should not be
watching files and the ones that must survive configuration being broken,
which is what the two questions below are for.
"""

from __future__ import annotations

import logging

from django.apps import AppConfig

from . import configure_from_settings, serving_process, should_watch

__all__ = ["DynamicConfigAppConfig"]

_log = logging.getLogger("dynamic_config_web.django")


class DynamicConfigAppConfig(AppConfig):  # type: ignore[misc]
    """Loads `DYNAMIC_CONFIG` when Django starts."""

    name = "dynamic_config_web.django"
    label = "dynamic_config"
    verbose_name = "dynamic-config"

    def ready(self) -> None:
        """Reads the setting, then loads and — where apt — watches.

        Idempotent: `ready()` runs again when a test re-populates the app
        registry, and :class:`~dynamic_config_web.Wiring` treats a start it
        is already running as nothing to do.

        **A serving process that cannot load its configuration does not
        start.** That is the whole value of loading here rather than at the
        first request: the failure lands in the deploy, where a rollback is
        one command, instead of in a worker that has already taken traffic.

        A management command is the deliberate exception. `configcheck` is
        the tool you reach for *because* configuration is broken, and a
        `migrate` that cannot run during an outage is a worse problem than
        the outage. Those load if they can and carry on if they cannot.
        """
        installation = configure_from_settings(watch=should_watch())

        try:
            installation.wiring.start()
        except Exception as refused:
            if serving_process():
                raise

            _log.warning(
                "dynamic-config could not be loaded (%s); the command will "
                "run without it. `manage.py configcheck` explains why.",
                refused,
            )
