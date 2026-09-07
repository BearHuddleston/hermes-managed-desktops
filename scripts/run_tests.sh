#!/usr/bin/env bash
# Delegate to stock Hermes's canonical hermetic per-file runner, not raw pytest.
# CI pins the exact clean stock revision in scripts/verify_ci.py and package.yml.
# HERMES_PYTHON is used only after the core runner probes its local/shared venvs.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ "$#" -lt 1 ]; then
    printf 'Usage: scripts/run_tests.sh /path/to/clean/hermes-agent [runner/pytest flags]\n' >&2
    exit 2
fi
HERMES_CHECKOUT="$(cd "$1" && pwd)"
shift
# The external tests must inherit collection-time isolation, not only fixtures.
# Importlib mode prevents this repository's root shim/tests from shadowing core.
exec "$HERMES_CHECKOUT/scripts/run_tests.sh" "$ROOT/tests" \
    -c "$HERMES_CHECKOUT/pyproject.toml" --rootdir="$HERMES_CHECKOUT" \
    -p tests.conftest --import-mode=importlib -o "pythonpath=$HERMES_CHECKOUT $ROOT" "$@"
