#!/usr/bin/env bash
# This distribution's release.
#
#   scripts/release-web.sh patch|minor|major|<version>   # prepare
#   scripts/release-web.sh --check patch|minor|<version> # would it work?
#   scripts/release-web.sh --publish                     # after it lands
#   scripts/release-web.sh --status                      # what is where
#
# `dynamic-config-py-web` versions independently of the wheels it sits on:
# it *depends* on `dynamic-config-py` rather than shipping it, so an engine
# release has nothing in it for an adapter and an adapter fix should not
# drag the wheels behind it.
#
# One distribution and no Cargo manifest, which is the whole difference from
# `release-python.sh`: the version lives in `src/dynamic_config_web/__init__.py`
# as `__version__`, and `[tool.hatch.version]` reads it from there.
#
# What it does *not* do: push, tag, or publish. Publishing is CI's, after
# the gates.
set -euo pipefail
cd "$(dirname "$0")/.."

module="src/dynamic_config_web/__init__.py"
changelog="CHANGELOG.md"
pypi="dynamic-config-py-web"
base_pypi="dynamic-config-py"

current() {
    sed -n 's/^__version__ = "\([^"]*\)".*/\1/p' "${module}" | head -1
}

# The floor this package puts on the base wheel. Not asserted against a
# version — they release independently — but printed, because "which engine
# does this need" is the first question a resolution failure raises.
declared_floor() {
    sed -n 's/.*dynamic-config-py>=\([0-9][0-9.]*\).*/\1/p' pyproject.toml | head -1
}

published() {
    curl -fsSL "https://pypi.org/pypi/$1/json" 2>/dev/null |
        python3 -c 'import json,sys; print(json.load(sys.stdin)["info"]["version"])' 2>/dev/null ||
        true
}

entries_under_unreleased() {
    awk '/^## \[Unreleased\]$/ { on = 1; next }
         /^## / { on = 0 }
         on && /^[-*] / { count++ }
         END { print count + 0 }' "$1"
}

bumped() {
    local version=$1 kind=$2
    local major minor patch
    IFS=. read -r major minor patch <<<"${version}"

    case "${kind}" in
        major) echo "$((major + 1)).0.0" ;;
        minor) echo "${major}.$((minor + 1)).0" ;;
        patch) echo "${major}.${minor}.$((patch + 1))" ;;
    esac
}

status() {
    echo "package:"
    echo "  ${pypi}   $(current)   (${module})"
    echo "  requires  ${base_pypi}>=$(declared_floor)"
    echo

    echo "changelog:"
    echo "  ${changelog}: $(entries_under_unreleased "${changelog}") entr(y|ies) under Unreleased"
    echo

    echo "on PyPI:"
    local there
    there=$(published "${pypi}")
    echo "  ${pypi}: ${there:-not published yet}"

    there=$(published "${base_pypi}")
    echo "  ${base_pypi}: ${there:-not published yet}   (the floor this needs)"
}

# Everything that has to be true before a version can be prepared, checked
# without changing anything. Run on its own, and again as the first thing
# `prepare` does.
check() {
    local target=${1:-$(current)}
    local problems=0

    local there
    there=$(published "${pypi}")

    if [[ -n ${there} && ${there} == "${target}" ]]; then
        echo "✗ ${pypi} ${target} is already on PyPI" >&2
        echo "  A version on PyPI is permanent; preparing this one again" >&2
        echo "  would publish nothing at all." >&2
        problems=$((problems + 1))
    fi

    # The floor has to be *resolvable*, or `pip install dynamic-config-py-web`
    # fails for everybody. This catches the ordering mistake the first
    # release could make: shipping this package before the base wheel it
    # needs is on PyPI.
    local base_there floor
    base_there=$(published "${base_pypi}")
    floor=$(declared_floor)

    if [[ -z ${base_there} ]]; then
        echo "✗ ${base_pypi} is not on PyPI at all, and this requires >=${floor}" >&2
        problems=$((problems + 1))
    elif [[ $(printf '%s\n%s\n' "${floor}" "${base_there}" | sort -V | head -1) != "${floor}" ]]; then
        echo "✗ this requires ${base_pypi}>=${floor}, and PyPI has ${base_there}" >&2
        echo "  Release the base wheel first, or lower the floor." >&2
        problems=$((problems + 1))
    fi

    if ! grep -q "^## \[Unreleased\]$" "${changelog}"; then
        echo "✗ no '## [Unreleased]' heading in ${changelog}" >&2
        problems=$((problems + 1))
    elif [[ $(entries_under_unreleased "${changelog}") -eq 0 ]]; then
        echo "✗ nothing under '## [Unreleased]' in ${changelog}" >&2
        echo "  A release with an empty section is a release nobody can read." >&2
        problems=$((problems + 1))
    fi

    if [[ ${problems} -eq 0 ]]; then
        echo "✓ ready to prepare ${target}"
        return 0
    fi

    return 1
}

publish() {
    local version
    version=$(current)

    echo "Dispatching the release for ${pypi} ${version}."
    echo
    echo "This builds one sdist and one wheel and uploads with"
    echo "--skip-existing, so a version already on PyPI is a no-op rather"
    echo "than an error — which is why '--check' refuses to prepare one."
    echo

    if [[ $(git rev-parse --abbrev-ref HEAD) != "main" ]]; then
        echo "note: you are not on main. The dispatch runs against main's"
        echo "      workflow file either way — make sure ${version} landed there."
        echo
    fi

    read -r -p "Dispatch release.yml? [y/N] " answer
    [[ ${answer,,} == y ]] || { echo "not dispatched."; exit 0; }

    gh workflow run release.yml --ref main
    echo
    echo "Watch it with: ./scripts/watch-release.sh"
}

prepare() {
    local requested=$1
    local from to
    from=$(current)

    case "${requested}" in
        major | minor | patch) to=$(bumped "${from}" "${requested}") ;;
        [0-9]*.[0-9]*.[0-9]*) to=${requested} ;;
        *)
            echo "usage: $0 patch|minor|major|<version>" >&2
            exit 2
            ;;
    esac

    if [[ -n $(git status --porcelain) ]]; then
        echo "the tree is dirty; commit or stash first" >&2
        exit 1
    fi

    check "${to}" || exit 1

    echo
    echo "${pypi}: ${from} → ${to}, with $(entries_under_unreleased "${changelog}") changelog entr(y|ies)."
    read -r -p "Prepare it? [y/N] " answer
    [[ ${answer,,} == y ]] || { echo "nothing changed."; exit 0; }

    python3 - "${from}" "${to}" "${module}" "${changelog}" <<'PY'
import datetime
import re
import sys

before, after, module, changelog = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]

with open(module, encoding="utf-8") as handle:
    text = handle.read()

text, count = re.subn(
    rf'^__version__ = "{re.escape(before)}"$',
    f'__version__ = "{after}"',
    text,
    count=1,
    flags=re.MULTILINE,
)

if count != 1:
    raise SystemExit(f'{module}: no __version__ = "{before}" to move')

with open(module, "w", encoding="utf-8") as handle:
    handle.write(text)

with open(changelog, encoding="utf-8") as handle:
    notes = handle.read()

heading = "## [Unreleased]"

if heading not in notes:
    raise SystemExit(f"{changelog}: no Unreleased heading")

# Unbracketed: this repository's tags are its own versions, and there is no
# link definition at the bottom to resolve a bracket to.
notes = notes.replace(
    heading, f"{heading}\n\n## {after} — {datetime.date.today().isoformat()}", 1
)

with open(changelog, "w", encoding="utf-8") as handle:
    handle.write(notes)
PY

    git add "${module}" "${changelog}"
    git commit -m "release ${pypi} ${to}"

    echo
    echo "Prepared, not pushed. What is left:"
    echo
    echo "  1. git push origin \$(git rev-parse --abbrev-ref HEAD)"
    echo "  2. ./scripts/promote.sh          # the PR, the gates, the merge"
    echo
    echo "Merging into main *is* the release: release.yml sees a version"
    echo "with no tag, builds, publishes and tags. '$0 --publish' is the"
    echo "manual door for a rerun."
}

case "${1:---status}" in
    --status) status ;;
    --check)
        # With a target, because the useful question is *would the next one
        # work* — the version this repository is on has, by definition,
        # already been released.
        case "${2:-}" in
            "") check ;;
            major | minor | patch) check "$(bumped "$(current)" "$2")" ;;
            [0-9]*.[0-9]*.[0-9]*) check "$2" ;;
            *)
                echo "usage: $0 --check [patch|minor|major|<version>]" >&2
                exit 2
                ;;
        esac
        ;;
    --publish) publish ;;
    -h | --help)
        sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
        ;;
    *) prepare "$1" ;;
esac
