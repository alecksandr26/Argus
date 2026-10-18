# Lambda wrapper around the UNCHANGED backend-argus image (src/backend-argus/Dockerfile, built
# and pytest-gated first by scripts/deploy.sh as argus/backend-argus:latest). Nothing in the
# backend's code or Dockerfile knows it runs on Lambda.
#
# The AWS Lambda Web Adapter is a Lambda extension: it turns each Lambda invocation into a plain
# HTTP request to the uvicorn process below and waits for /health before taking traffic.
ARG BASE_IMAGE=argus/backend-argus:latest
FROM ${BASE_IMAGE}

COPY --from=public.ecr.aws/awsguru/aws-lambda-adapter:0.9.1 /lambda-adapter /opt/extensions/lambda-adapter
COPY load_ssm_env.py /opt/argus/load_ssm_env.py

ENV PORT=8000 \
    AWS_LWA_PORT=8000 \
    AWS_LWA_READINESS_CHECK_PATH=/health \
    PYTHONDONTWRITEBYTECODE=1

# Same uvicorn command as the base image, prefixed by the SSM secret loader.
CMD ["python", "/opt/argus/load_ssm_env.py", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
