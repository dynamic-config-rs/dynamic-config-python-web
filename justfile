# Everything CI runs, in the order that fails fastest.
#
# No Rust here: this distribution is pure Python over the compiled wheel.
# What it needs is an environment with `dynamic-config-py` installed and
# whichever frameworks you are working on — `pip install -e '.[dev]'` gets
# the five Beta ones.

default: check

# The whole gate, locally.
check: lint types core adapters django-app examples

# Formatting and lint, as CI checks them.
lint:
    ruff check .
    ruff format --check .

# The types a caller sees. The frameworks are `ignore_missing_imports`, so
# this runs with or without them — and checks against the real thing when
# they are there.
types:
    mypy --strict src/dynamic_config_web/

# The shared half. Runs with no framework installed, which is the point:
# the core is what has to work on every interpreter this package claims.
core:
    python -m pytest tests/test_core.py tests/test_packaging.py -q

# Every adapter whose framework is installed; the rest skip themselves.
adapters:
    python -m pytest tests/conformance -q

# One framework's conformance run, by name: `just adapter fastapi`.
adapter name:
    python -m pytest tests/conformance/test_{{ name }}.py -q

# The path a real Django project takes, which no conformance row reaches:
# `INSTALLED_APPS`, `AppConfig.ready()`, and the rule deciding which
# processes watch. Needs `django` — and `django-ninja` for one case.
django-app:
    python -m pytest tests/test_django_app.py -q

# The examples are documentation people run, so they run here. Each one
# writes into a temporary directory and needs no setup.
examples:
    #!/usr/bin/env bash
    set -euo pipefail
    for example in examples/[0-9]*.py; do
        echo "→ $example"
        python "$example" > /dev/null
    done

# This repository's book. The docs site builds it alongside the other four
# and publishes all five together; this is the same build, alone.
# Needs mdbook (`cargo install mdbook`).
book:
    mdbook build book
    test -f book/book/index.html

# What the extras resolve to, which no lockfile here records — a library
# that pinned its dependencies would pin its users'.
audit:
    python scripts/resolve-web-audit.py /tmp/web-audit-venv /tmp/web-requirements.txt
