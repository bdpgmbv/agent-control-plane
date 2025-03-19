"""
Write a Dockerfile and docker-compose.yml for each project.

One generator rather than six hand-written files, because they now all have the
same shape: a src/ layout with a pyproject.toml. Before this, 01-04 had
containers and 05-06 had none, and the four that existed still copied `app/`,
which no longer exists.

The build installs the package rather than copying source onto the PYTHONPATH.
That is the point of having a pyproject: the container runs the same installed
artefact a developer runs, instead of a directory that happens to be importable
because of where the process started.
"""

from pathlib import Path

SERIES_ROOT = Path(__file__).resolve().parents[1]

DOCKERFILE = '''# %(title)s
#
# Built in two stages so that editing the code does not reinstall the world.
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \\
    PYTHONUNBUFFERED=1 \\
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
%(extra_copies)s
# A non-root user, and a writable place for the database.
RUN useradd --create-home --uid 10001 service \\
 && mkdir -p /srv/data \\
 && chown -R service:service /srv
USER service

EXPOSE %(port)d

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \\
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:%(port)d/api/health')"

CMD ["uvicorn", "%(target)s", "--host", "0.0.0.0", "--port", "%(port)d"]
'''

COMPOSE = '''services:
  %(service)s:
    build: .
    image: %(service)s:1.0.0
    ports:
      - "%(port)d:%(port)d"
    environment:
      # Nothing secret is baked into the image. Without a key the service still
      # runs - every project in this series works offline.
      OPENAI_API_KEY: "${OPENAI_API_KEY:-}"
      HOST: "0.0.0.0"
      PORT: "%(port)d"
    env_file:
      - path: .env
        required: false
    volumes:
      # State outlives the container; source does not go in.
      - %(service)s-data:/srv/data
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:%(port)d/api/health')"]
      interval: 30s
      timeout: 5s
      retries: 3

volumes:
  %(service)s-data:
'''

PROJECTS = [
    ("01_rag_knowledge_assistant", "rag-assistant", 8010,
     "rag_assistant.layer7_api.main:app", "RAG Knowledge Assistant",
     ["ui", "samples"]),
    ("02_customer_support_agent", "support-agent", 8020,
     "support_agent.layer7_api.main:app", "AI Customer Support Agent",
     ["ui"]),
    ("03_agentic_research", "research-agent", 8030,
     "research_agent.layer8_api.main:app", "Agentic Research System",
     ["ui"]),
    ("04_nl_to_sql_copilot", "sql-copilot", 8040,
     "sql_copilot.layer9_api.main:app", "NL to SQL Analytics Copilot",
     ["ui"]),
    ("05_document_intelligence", "doc-intelligence", 8050,
     "doc_intelligence.layer9_api.app:app", "Document Intelligence Pipeline",
     ["ui", "samples"]),
    ("06_coding_agent", "coding-agent", 8060,
     "coding_agent.layer9_api.app:app", "AI Coding Agent",
     ["ui", "benchmark"]),
    ("07_enterprise_workflow", "enterprise-workflow", 8070,
     "enterprise_workflow.layer9_api.app:app", "Enterprise Multi-Agent Workflow",
     ["ui"]),
    ("08_llm_gateway", "llm-gateway", 8080,
     "llm_gateway.layer9_api.app:app", "LLM Gateway and Evaluation Platform",
     ["ui"]),
]


def main() -> int:
    for folder, service, port, target, title, extras in PROJECTS:
        project = SERIES_ROOT / folder
        if not project.is_dir():
            continue

        copies = []
        for name in extras:
            if (project / name).is_dir():
                copies.append("COPY %s ./%s" % (name, name))
        extra_copies = "\n".join(copies) + "\n" if len(copies) > 0 else ""

        (project / "Dockerfile").write_text(DOCKERFILE % {
            "title": title, "port": port, "target": target,
            "extra_copies": extra_copies,
        })
        (project / "docker-compose.yml").write_text(COMPOSE % {
            "service": service, "port": port,
        })
        (project / ".dockerignore").write_text(
            "\n".join([
                "# Anything the image must never contain.",
                ".env", ".venv/", "venv/",
                "__pycache__/", "*.pyc", ".pytest_cache/", ".mypy_cache/",
                ".ruff_cache/", "*.egg-info/", "build/", "dist/",
                "data/", "workspaces/", "eval_results/", "*.db", "*.sqlite3",
                ".git/", ".github/", "tests/", "scripts/", ".DS_Store", "",
            ])
        )
        print("  %-32s Dockerfile, compose, .dockerignore  (port %d)" % (folder, port))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
