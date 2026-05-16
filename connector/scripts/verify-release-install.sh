#!/usr/bin/env bash
set -euo pipefail

UV_BIN="${UV_BIN:-uv}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST_DIR="$ROOT_DIR/dist"
TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/pocage-release-check.XXXXXX")"
KEEP_TMP="${KEEP_TMP:-0}"

cleanup() {
  rm -rf "$ROOT_DIR/build" "$ROOT_DIR/pocage.egg-info"
  if [[ "$KEEP_TMP" == "1" ]]; then
    echo "kept temporary verification directory: $TMP_DIR"
    return
  fi
  rm -rf "$TMP_DIR"
}

trap cleanup EXIT

if ! command -v "$UV_BIN" >/dev/null 2>&1; then
  echo "uv not found: $UV_BIN"
  exit 1
fi

find_artifact() {
  local pattern="$1"
  find "$DIST_DIR" -maxdepth 1 -type f -name "$pattern" | sort | tail -n 1
}

verify_artifact_install() {
  local kind="$1"
  local artifact="$2"
  local env_dir="$TMP_DIR/$kind-env"
  local python_bin="$env_dir/bin/python"
  local pocage_bin="$env_dir/bin/pocage"

  echo "==> verifying $kind install: $(basename "$artifact")"
  "$UV_BIN" venv "$env_dir" >/dev/null
  "$UV_BIN" pip install --python "$python_bin" "$artifact" >/dev/null
  "$UV_BIN" pip check --python "$python_bin" >/dev/null

  (
    cd "$TMP_DIR"
    "$python_bin" - "$ROOT_DIR" "$kind" <<'PY'
import importlib.metadata as metadata
import json
import pathlib
import sys

import pocage

project_root = pathlib.Path(sys.argv[1]).resolve()
kind = sys.argv[2]
module_path = pathlib.Path(pocage.__file__).resolve()

if project_root in module_path.parents:
    raise SystemExit(f"{kind} install is still loading source tree: {module_path}")
if "site-packages" not in module_path.parts:
    raise SystemExit(f"{kind} install did not land in site-packages: {module_path}")

dist = metadata.distribution("pocage")
direct_url_path = pathlib.Path(dist._path) / "direct_url.json"
if direct_url_path.exists():
    data = json.loads(direct_url_path.read_text())
    if data.get("dir_info", {}).get("editable") is True:
        raise SystemExit(f"{kind} install is unexpectedly editable")

print(f"{kind} import OK: {module_path}")
PY
  )

  (
    cd "$TMP_DIR"
    "$pocage_bin" --help >/dev/null
  )
  echo "==> $kind install OK"
}

cd "$ROOT_DIR"

echo "==> building distributions"
"$UV_BIN" build

WHEEL_PATH="$(find_artifact 'pocage-*.whl')"
SDIST_PATH="$(find_artifact 'pocage-*.tar.gz')"

if [[ -z "$WHEEL_PATH" || -z "$SDIST_PATH" ]]; then
  echo "missing built artifacts in $DIST_DIR"
  exit 1
fi

verify_artifact_install "wheel" "$WHEEL_PATH"
verify_artifact_install "sdist" "$SDIST_PATH"

echo "==> release install verification passed"
