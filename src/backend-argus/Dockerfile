# Multi-stage: this is a plain FastAPI service, no native model artifacts or platform-specific
# wheels to bake in (contrast src/cv-argus's Dockerfile). Debian slim (glibc), not Alpine —
# bcrypt's C extension and Motor/pymongo's C driver bits build more reliably against glibc, and
# it keeps a consistent base-image story with cv-argus's own Debian-slim choice rather than
# introducing a musl-only edge case in just this one module.
FROM python:3.12-slim-bookworm AS base

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY scripts ./scripts

# --- test gate ---------------------------------------------------------------------------
# Runs the hermetic pytest tier (mongomock-motor, no real Mongo — see pyproject.toml's
# `addopts = -m 'not mongo'`) as part of the build itself, not as a separate CI step. `runtime`
# below is FROM `base`, not FROM this stage, and pulls back only `/app/app` via `COPY
# --from=test` — a no-op content-wise (test's `/app/app` is identical to base's, it only added
# tests/ and pyproject.toml alongside it) that exists purely to force Docker to build and pass
# this stage before `runtime` can be built at all. That's what makes a failing test fail the
# whole `docker build`, while keeping `tests/`/`pyproject.toml` out of the runtime image
# entirely — they never get copied into `runtime`'s own layers.
FROM base AS test
COPY tests ./tests
COPY pyproject.toml .
RUN pytest

FROM base AS runtime
COPY --from=test /app/app ./app

ENV PYTHONUNBUFFERED=1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
