"""`manage.py configcheck` — would this deployment's configuration load.

    $ python manage.py configcheck
    $ python manage.py configcheck --explain database.port
    $ python manage.py configcheck --config db --strict

Exits non-zero when a configuration would not load, which is what makes it
useful in a container's start script and in CI: the failure happens in a
command whose output you read, rather than in a worker whose first request
you do not.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError

from dynamic_config_web import check
from dynamic_config_web.django import installation

__all__ = ["Command"]


class Command(BaseCommand):  # type: ignore[misc]
    """Loads every configured configuration and reports on it."""

    help = "Check that this deployment's dynamic configuration would load."

    def add_arguments(self, parser: Any) -> None:
        """`--config`, `--explain`, `--strict`."""
        parser.add_argument(
            "--config",
            help="only this configuration, by key (default: all of them)",
        )
        parser.add_argument(
            "--explain",
            metavar="PATH",
            help="show every layer's answer for one dotted path",
        )
        parser.add_argument(
            "--strict",
            action="store_true",
            help="fail on unknown keys as well as on a refusal",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Prints a report per configuration, and fails if one is bad."""
        del args

        install = installation()
        configs = (
            [install.config(options["config"])]
            if options["config"]
            else list(install.wiring.configs)
        )

        if options["explain"]:
            for config in configs:
                self.stdout.write(self.style.MIGRATE_HEADING(config.key or "<root>"))
                self.stdout.write(str(config.explain(options["explain"])))

            return

        bad: list[str] = []

        for config in configs:
            # The shared report rather than the engine's, because that one
            # answers both halves: the keys resolve *and* the model builds.
            report = check(config)

            self.stdout.write(self.style.MIGRATE_HEADING(config.key or "<root>"))
            self.stdout.write(report["rendered"])

            if report["failure"] is not None:
                bad.append(f"{config.key}: {report['failure']}")
            elif options["strict"] and report["unknown"]:
                paths = ", ".join(key["path"] for key in report["unknown"])
                bad.append(f"{config.key}: unknown keys ({paths})")
            else:
                # It loads, and the model's type is the proof — its type
                # and not its contents, because a command's output ends up
                # in a CI log.
                self.stdout.write(
                    self.style.SUCCESS(f"ok — {config.model.__name__} would load")
                )

        if bad:
            raise CommandError("configuration would not load:\n  " + "\n  ".join(bad))
