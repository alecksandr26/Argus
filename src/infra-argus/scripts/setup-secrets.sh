#!/usr/bin/env bash
# One-time: create the three SSM SecureString parameters the stacks read (free, standard tier).
# Prompts for each value so nothing lands in shell history. Re-run to rotate (overwrites).
source "$(dirname "$0")/lib.sh"
require aws

put() {
  local name="$1" prompt="$2" value
  read -r -s -p "$prompt: " value; echo
  [ -n "$value" ] || { echo "Empty value, aborting" >&2; exit 1; }
  aws ssm put-parameter --name "$name" --type SecureString --value "$value" --overwrite >/dev/null
  echo "  saved $name"
}

put /argus/mongo-uri "MongoDB Atlas connection string (mongodb+srv://...)"
put /argus/jwt-secret "JWT secret (any long random string, e.g. from: openssl rand -hex 32)"
put /argus/root-admin-password "Root admin password (used to log in, and by the simulator)"
