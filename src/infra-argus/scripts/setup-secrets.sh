#!/usr/bin/env bash
# Push the three secrets to SSM SecureString parameters (free, standard tier), where the deployed
# backend loads them at startup. Values come from src/infra-argus/.env (gitignored, see
# .env.example) or the shell environment; anything missing is prompted for (input hidden).
# Re-run to rotate (overwrites).
source "$(dirname "$0")/lib.sh"
require aws

put() {
  local name="$1" var="$2" prompt="$3" value="${!2:-}"
  if [ -z "$value" ]; then
    read -r -s -p "$prompt: " value; echo
  fi
  [ -n "$value" ] || { echo "Empty $var, aborting" >&2; exit 1; }
  [[ "$value" != *"<"* ]] || { echo "$var still has a <placeholder> in it, aborting" >&2; exit 1; }
  aws ssm put-parameter --name "$name" --type SecureString --value "$value" --overwrite >/dev/null
  echo "  saved $name (from $var)"
}

echo "Writing secrets to SSM in region ${AWS_REGION:-<aws default>}"
put /argus/mongo-uri MONGODB_URI "MongoDB Atlas connection string (mongodb+srv://...)"
put /argus/jwt-secret JWT_SECRET "JWT secret (any long random string, e.g. from: openssl rand -hex 32)"
put /argus/root-admin-password ROOT_ADMIN_PASSWORD "Root admin password (used to log in, and by the simulator)"
