"""
Move one project from app/ to a real src/ package layout.

The numbered layer and step names are KEPT. What changes is everything around
them: the code becomes an installable package under src/, so imports resolve
because the package is installed rather than because of where you happened to
run python from.

    python3 tools/restructure.py 06_coding_agent coding_agent

Seven things happen, and each one is verified rather than assumed:

  1. app/           -> src/<package>/            (via git mv, so history follows)
  2. from app.x     -> from <package>.x          in every .py, and in the
                                                 uvicorn strings in Makefile,
                                                 run.sh, Dockerfile, launch.json
  3. parents[2]     -> parents[3]                inside the package only - every
                                                 file is one directory deeper now
  4. sys.path hacks removed from scripts/        an installed package does not
                                                 need them
  5. pyproject.toml written                      with package-data for the json
                                                 and ini files that live in the
                                                 package
  6. pytest.ini updated                          pythonpath no longer needed
  7. .gitignore gains build artefacts

Run with --check to see what it would do without doing it.
"""

import re
import subprocess
import sys
from pathlib import Path

SERIES_ROOT = Path(__file__).resolve().parents[1]


def run(command, cwd=None):
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError("%s failed: %s" % (" ".join(command), result.stderr.strip()))
    return result.stdout


def python_files_under(folder: Path) -> list[Path]:
    found = []
    for path in folder.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        found.append(path)
    return found


def rewrite_imports(paths: list[Path], package: str) -> int:
    """`from app.x` and `import app.x` become the package name."""
    changed = 0
    for path in paths:
        source = path.read_text()
        before = source
        source = re.sub(r"\bfrom app\.", "from %s." % package, source)
        source = re.sub(r"\bimport app\.", "import %s." % package, source)
        source = re.sub(r'(["\'])app\.layer', r"\1%s.layer" % package, source)
        if source != before:
            path.write_text(source)
            changed = changed + 1
    return changed


def deepen_project_root(paths: list[Path]) -> int:
    """
    Every file in the package sits one directory deeper than it did.

    This is the change most likely to be silently wrong: parents[2] still
    resolves to A directory, just the wrong one, so nothing raises - the project
    simply starts reading .env and samples from src/ and finding nothing there.
    """
    changed = 0
    for path in paths:
        source = path.read_text()
        before = source
        source = source.replace("parents[2]", "parents[3]")
        if source != before:
            path.write_text(source)
            changed = changed + 1
    return changed


def remove_path_hacks(scripts_folder: Path) -> int:
    """An installed package does not need sys.path.insert."""
    if not scripts_folder.is_dir():
        return 0

    changed = 0
    for path in python_files_under(scripts_folder):
        lines = path.read_text().split("\n")
        kept = []
        removed_any = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("sys.path.insert(0, str(Path(__file__)"):
                removed_any = True
                continue
            kept.append(line)
        if removed_any:
            path.write_text("\n".join(kept))
            changed = changed + 1
    return changed


PYPROJECT = '''[build-system]
requires = ["setuptools>=68", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "%(dist)s"
version = "1.0.0"
description = "%(description)s"
requires-python = ">=3.12,<3.14"
dependencies = [
%(dependencies)s]

[project.optional-dependencies]
dev = ["pytest==8.3.4", "ruff==0.8.4", "mypy==1.14.0", "httpx==0.28.1"]

[tool.setuptools]
package-dir = {"" = "src"}

[tool.setuptools.packages.find]
where = ["src"]

# Data files that live inside the package - golden datasets, the sandbox
# pytest config. Without this they are missing from an installed copy and the
# evaluation suite fails with a confusing FileNotFoundError.
[tool.setuptools.package-data]
"*" = ["*.json", "*.ini", "*.txt", "*.md"]

[tool.ruff]
line-length = 100
target-version = "py312"
src = ["src", "tests", "scripts"]

[tool.ruff.lint]
# C4 (comprehensions) is deliberately NOT enabled: this codebase uses explicit
# loops on purpose so it can be read by someone learning the system.
select = ["E", "F", "W", "I", "UP", "B"]
ignore = [
    "E501",   # line length is handled by the formatter, not enforced here
    "B008",   # FastAPI's Depends() in a default argument is the documented idiom
]

[tool.mypy]
python_version = "3.12"
files = ["src"]
ignore_missing_imports = true
# Deliberately not strict. These projects are built to be read, and full
# strictness would fill them with annotations that teach nothing.
check_untyped_defs = true

[tool.pytest.ini_options]
testpaths = ["tests"]
python_files = ["test_*.py"]
addopts = "-q --tb=short"
'''


def write_pyproject(project: Path, package: str, description: str) -> None:
    requirements = project / "requirements.txt"
    dependency_lines = []
    if requirements.is_file():
        for line in requirements.read_text().split("\n"):
            line = line.strip()
            if line == "" or line.startswith("#"):
                continue
            # pytest and httpx belong in the dev extra, not the runtime deps.
            if line.startswith("pytest") or line.startswith("httpx"):
                continue
            dependency_lines.append('    "%s",\n' % line)

    (project / "pyproject.toml").write_text(PYPROJECT % {
        "dist": package.replace("_", "-"),
        "description": description,
        "dependencies": "".join(dependency_lines),
    })


def update_support_files(project: Path, package: str) -> None:
    """Makefile, run.sh, Dockerfile, launch.json and pytest.ini."""
    for name in ("Makefile", "run.sh", "Dockerfile", "docker-compose.yml",
                 ".claude/launch.json"):
        path = project / name
        if not path.is_file():
            continue
        source = path.read_text()
        before = source
        source = source.replace("app.layer", "%s.layer" % package)
        # Docker copies the source tree.
        source = source.replace("COPY app/", "COPY src/")
        source = source.replace("COPY ./app", "COPY ./src")
        if source != before:
            path.write_text(source)

    # pytest.ini is superseded by [tool.pytest.ini_options] in pyproject.toml,
    # and having both is a coin toss over which one pytest reads.
    old_pytest_ini = project / "pytest.ini"
    if old_pytest_ini.is_file():
        old_pytest_ini.unlink()


def restructure(project_folder: str, package: str, description: str,
                check_only: bool = False) -> None:
    project = SERIES_ROOT / project_folder
    app_folder = project / "app"
    source_folder = project / "src"
    destination = source_folder / package

    if not app_folder.is_dir():
        print("  %s: no app/ folder - already restructured?" % project_folder)
        return

    print("  %s -> src/%s/" % (project_folder, package))
    if check_only:
        return

    # ---- 1. move ----
    source_folder.mkdir(exist_ok=True)
    run(["git", "mv", str(app_folder), str(destination)], cwd=SERIES_ROOT)

    # ---- 2 & 3. imports and depth, inside the package ----
    package_files = python_files_under(destination)
    imports_changed = rewrite_imports(package_files, package)
    depth_changed = deepen_project_root(package_files)

    # ---- 2 again, outside the package ----
    outside = []
    for folder in ("tests", "scripts"):
        outside.extend(python_files_under(project / folder))
    outside_changed = rewrite_imports(outside, package)

    # ---- 4. no more path games ----
    hacks_removed = remove_path_hacks(project / "scripts")

    # ---- 5, 6, 7 ----
    write_pyproject(project, package, description)
    update_support_files(project, package)

    print("     imports rewritten: %d in the package, %d in tests/scripts"
          % (imports_changed, outside_changed))
    print("     parents[2] -> parents[3]: %d file(s)" % depth_changed)
    print("     sys.path hacks removed:  %d file(s)" % hacks_removed)


PROJECTS = [
    ("01_rag_knowledge_assistant", "rag_assistant",
     "Production RAG knowledge assistant with hybrid retrieval and honest refusal"),
    ("02_customer_support_agent", "support_agent",
     "AI customer support agent with permissioned tools and human approval"),
    ("03_agentic_research", "research_agent",
     "Agentic research system with parallel workers on one shared budget"),
    ("04_nl_to_sql_copilot", "sql_copilot",
     "Natural language to SQL copilot with validation before execution"),
    ("05_document_intelligence", "doc_intelligence",
     "Document intelligence pipeline: classify, extract, validate, decide"),
    ("06_coding_agent", "coding_agent",
     "AI coding agent that is refused when its patch cheats"),
]


def main() -> int:
    check_only = "--check" in sys.argv
    wanted = None
    for argument in sys.argv[1:]:
        if not argument.startswith("--"):
            wanted = argument
            break

    for folder, package, description in PROJECTS:
        if wanted is not None and folder != wanted and package != wanted:
            continue
        restructure(folder, package, description, check_only)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
