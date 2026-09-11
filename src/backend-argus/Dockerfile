# Single-stage: this is a plain FastAPI service, no native model artifacts or platform-specific
# wheels to bake in (contrast src/cv-argus's Dockerfile). Debian slim (glibc), not Alpine —
# bcrypt's C extension and Motor/pymongo's C driver bits build more reliably against glibc, and
# it keeps a consistent base-image story with cv-argus's own Debian-slim choice rather than
# introducing a musl-only edge case in just this one module.
FROM python:3.12-slim-bookworm

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

ENV PYTHONUNBUFFERED=1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
