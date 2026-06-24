#!/bin/sh
# Dev launcher. Uses uv to manage the environment.
# For end-user install, see README (uv tool install).
set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
SCANNER="$DIR/storagemark/c/storagescanner"

# Build C scanner if missing or stale
if [ ! -f "$SCANNER" ] || [ "$DIR/storagemark/c/storagescanner.c" -nt "$SCANNER" ]; then
    echo "Building C scanner..."
    make -C "$DIR/storagemark/c" -s
fi

ROOT="${1:-.}"
shift 2>/dev/null || true

exec uv run --project "$DIR" python -m storagemark.python "$ROOT" "$@"
