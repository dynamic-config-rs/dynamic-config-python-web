#!/usr/bin/env bash
# Names the files a change has to travel to, the moment it is made.
#
# AGENTS.md lists seven things that move together when an adapter does;
# this is that list, said at the point where forgetting it is easy.
#
# Advisory by design: it exits 0 and never blocks a tool call.
set -euo pipefail

input=$(cat)
path=$(printf '%s' "$input" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("tool_input", {}).get("file_path", ""))' 2>/dev/null || true)

[ -z "$path" ] && exit 0

case "$path" in
  */src/dynamic_config_web/_*.py)
    cat <<'NOTE'
The shared core moved. Every adapter reads it, so check:
  · tests/test_core.py                  the behaviour, not the call
  · tests/conformance/suite.py          if the contract itself changed
  · the book page that states the rule  (rules/scope/health/diagnostics)
  · CHANGELOG.md                        under Unreleased
NOTE
    ;;
  */src/dynamic_config_web/django/*.py | */src/dynamic_config_web/*.py)
    cat <<'NOTE'
An adapter moved. AGENTS.md lists what travels with it:
  · tests/conformance/test_<framework>.py   a driver, nothing else
  · tests/test_packaging.py                 a row in ADAPTERS
  · examples/NN_<framework>.py              runnable, and run in CI
  · book/src/<framework>.md + SUMMARY.md
  · pyproject.toml                          the extra and its floor
  · .github/workflows/ci.yml                a matrix row
  · CHANGELOG.md                            under Unreleased
NOTE
    ;;
  */tests/conformance/suite.py)
    cat <<'NOTE'
A case added here is a case EVERY adapter must answer — that is the point
of the file. Run `just adapters` before deciding it passes, and if one
framework cannot answer it, skip it by name in `cases()` and write it down
in book/src/limitations.md rather than quietly.
NOTE
    ;;
  */pyproject.toml)
    cat <<'NOTE'
Packaging moved. Raising an extra's floor past a release people are on is a
BREAKING change (RELEASING.md), and `[all]` deliberately excludes the two
Experimental adapters. If an extra was added, `dynamic-config-python`'s own
pyproject.toml needs the matching name so `dynamic-config-py[...]` resolves.
NOTE
    ;;
esac

exit 0
