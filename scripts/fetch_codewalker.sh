#!/usr/bin/env bash
# Fetches CodeWalker's source (build-time dependency for tools/CodeWalkerCli,
# NOT vendored/committed -- CodeWalker has no explicit OSS license file in
# its repo, so we clone it at build time rather than bundle its source;
# see docs/architecture-decisions.md point 10) into third_party/CodeWalker
# (gitignored). Pins a commit for reproducibility.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
TARGET_DIR="$PROJECT_ROOT/third_party/CodeWalker"
REPO_URL="https://github.com/dexyfex/CodeWalker.git"
# Pin: verified to build CodeWalker.Core on Linux/.NET 8 on 2026-10-11.
PIN_COMMIT="${CODEWALKER_PIN_COMMIT:-}"

mkdir -p "$PROJECT_ROOT/third_party"

if [ -d "$TARGET_DIR/.git" ]; then
    echo "CodeWalker already present at $TARGET_DIR, fetching latest..."
    git -C "$TARGET_DIR" fetch --depth 1 origin
else
    git clone --depth 1 "$REPO_URL" "$TARGET_DIR"
fi

if [ -n "$PIN_COMMIT" ]; then
    git -C "$TARGET_DIR" checkout "$PIN_COMMIT"
fi

echo "CodeWalker source ready at $TARGET_DIR"
echo "Build the CLI with: dotnet publish tools/CodeWalkerCli -c Release -o tools/CodeWalkerCli/publish"
