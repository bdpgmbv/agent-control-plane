# RAG Knowledge Assistant
#
# Built in two stages so that editing the code does not reinstall the world.
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /srv

# Dependencies first. This layer is cached until requirements.txt changes.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Then the package itself, installed rather than copied onto the path - the
# container runs the same artefact a developer installs.
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir --no-deps .

# Everything the running service reads from disk.
COPY ui ./ui
COPY samples ./samples

# A non-root user, and a writable place for the database.
RUN useradd --create-home --uid 10001 service \
 && mkdir -p /srv/data \
 && chown -R service:service /srv
USER service

EXPOSE 8010

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8010/api/health')"

CMD ["uvicorn", "rag_assistant.layer7_api.main:app", "--host", "0.0.0.0", "--port", "8010"]
