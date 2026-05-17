#!/bin/sh
set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$DIR/.venv/bin/python"
SCANNER="$DIR/storagemark/c/storagescanner"

# Build C scanner if missing or stale
if [ ! -f "$SCANNER" ] || [ "$DIR/storagemark/c/storagescanner.c" -nt "$SCANNER" ]; then
    echo "Building C scanner..."
    make -C "$DIR/storagemark/c" -s
fi

ROOT="${1:-.}"
shift 2>/dev/null || true

exec "$VENV" -m storagemark.python "$ROOT" "$@"
