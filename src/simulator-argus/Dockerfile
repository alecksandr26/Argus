# Plain async HTTP client -- no native model artifacts or platform-specific wheels, unlike
# src/cv-argus. Debian slim (glibc), matching backend-argus's own base-image choice rather than
# introducing a musl-only edge case in just this module.
FROM python:3.12-slim-bookworm AS base

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY simulator_argus ./simulator_argus

# --- test gate ---------------------------------------------------------------------------
# Same mechanism as backend-argus/Dockerfile's own test-gate stage: `runtime` below is FROM
# `base`, not FROM `test`, and pulls back only /app/simulator_argus via `COPY --from=test` -- a
# no-op content-wise that exists purely to force Docker to build and pass this stage before
# `runtime` can be built at all, without tests/pyproject.toml ever reaching the runtime image.
FROM base AS test
COPY tests ./tests
COPY pyproject.toml .
RUN pytest

FROM base AS runtime
COPY --from=test /app/simulator_argus ./simulator_argus

ENV PYTHONUNBUFFERED=1

CMD ["python", "-m", "simulator_argus"]
