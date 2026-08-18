#!/usr/bin/env bash

set -Eeuo pipefail

REPO_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
PYTHON="$REPO_ROOT/.venv/bin/python"

if [ ! -x "$PYTHON" ]; then
  printf '%s\n' "MELvan's Python environment is unavailable at $PYTHON" >&2
  printf '%s\n' "Create it with: python3.12 -m venv .venv" >&2
  printf '%s\n' "Then install MELvan with: .venv/bin/python -m pip install -e '.[foundry]'" >&2
  exit 1
fi

cd "$REPO_ROOT"
exec env PYTHONPATH="$REPO_ROOT/src" "$PYTHON" -m melvan.dev "$@"
