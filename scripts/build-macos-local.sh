#!/bin/bash
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"

RUSTUP_BIN=$(brew --prefix rustup)/bin
export PATH="$RUSTUP_BIN:$PATH"
export RUSTFLAGS="-L native=/Library/Developer/CommandLineTools/usr/lib/swift/macosx${RUSTFLAGS:+ $RUSTFLAGS}"
export MACOSX_DEPLOYMENT_TARGET="${MACOSX_DEPLOYMENT_TARGET:-15.0}"

npm run tauri build -- --bundles app --config src-tauri/tauri.adhoc.conf.json -- --locked
