#!/usr/bin/env bash
# Shared helpers for the infra-argus scripts. Sourced, not run.
set -euo pipefail

INFRA_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC_DIR="$(cd "$INFRA_DIR/.." && pwd)"

# stack_output <StackName> <OutputKey>
stack_output() {
  aws cloudformation describe-stacks --stack-name "$1" \
    --query "Stacks[0].Outputs[?OutputKey=='$2'].OutputValue" --output text
}

require() {
  for bin in "$@"; do
    command -v "$bin" >/dev/null 2>&1 || { echo "Missing '$bin' on PATH (see src/infra-argus/README.md prerequisites)" >&2; exit 1; }
  done
}
