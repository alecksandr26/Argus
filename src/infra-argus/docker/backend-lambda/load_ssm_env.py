"""Load secrets from SSM Parameter Store into env vars, then exec the real command.

ARGUS_SSM_ENV="MONGO_URI=/argus/mongo-uri,JWT_SECRET=/argus/jwt-secret" fetches each parameter
(SecureString, decrypted) and sets it as that env var before `exec`-ing argv[1:]. This keeps the
secrets out of the CloudFormation template and the Lambda console's env var view, without any
change to backend-argus's own settings code (it just reads env vars as always).

Standard-tier SSM parameters are free; this runs once per Lambda cold start.
"""
import os
import sys

import boto3


def main() -> None:
    spec = os.environ.get("ARGUS_SSM_ENV", "")
    pairs = [p.split("=", 1) for p in spec.split(",") if "=" in p]
    if pairs:
        names = [path for _, path in pairs]
        resp = boto3.client("ssm").get_parameters(Names=names, WithDecryption=True)
        if resp.get("InvalidParameters"):
            sys.exit(f"Missing SSM parameters: {resp['InvalidParameters']} (see src/infra-argus/README.md)")
        values = {p["Name"]: p["Value"] for p in resp["Parameters"]}
        for env_name, path in pairs:
            os.environ[env_name] = values[path]
    os.execvp(sys.argv[1], sys.argv[1:])


if __name__ == "__main__":
    main()
