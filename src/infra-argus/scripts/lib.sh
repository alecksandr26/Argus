#!/usr/bin/env bash
# Shared helpers for the infra-argus scripts. Sourced, not run.
set -euo pipefail

INFRA_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC_DIR="$(cd "$INFRA_DIR/.." && pwd)"

# Local, gitignored settings/secrets (see .env.example). Variables already set in the shell win.
if [ -f "$INFRA_DIR/.env" ]; then
  while IFS='=' read -r key value; do
    [[ "$key" =~ ^[A-Z_][A-Z0-9_]*$ ]] || continue          # skip comments/blank lines
    value="${value%$'\r'}"                                   # tolerate Windows line endings
    if [[ "$value" =~ ^\"(.*)\"$ || "$value" =~ ^\'(.*)\'$ ]]; then value="${BASH_REMATCH[1]}"; fi
    [ -n "${!key:-}" ] || export "$key=$value"
  done < "$INFRA_DIR/.env"
fi

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
