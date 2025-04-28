"""
LAYER 3, STEP 1 - THE WORKING COPY
==================================
The agent never touches the real repository. It gets a copy in its own directory
and every path it names is resolved and checked against that directory.

WHAT THIS IS, HONESTLY
----------------------
This is a blast-radius limiter, not a security boundary.

It stops an agent from editing files outside its workspace by mistake or by being
talked into it, and it stops a bad patch from damaging the original repository.
It does NOT contain code the agent causes to RUN. The test suite is executed as a
normal subprocess with the same permissions this process has, so a test file that
calls `shutil.rmtree` on a home directory will do exactly that.

Running genuinely untrusted code needs a container, a separate user, seccomp, or
a VM - something the operating system enforces. Saying "sandbox" and meaning
"I checked the paths" is how people end up believing they have a boundary they do
not have, so this file says which one it is.

What follows from that: the agent may not edit test files (layer 8 enforces it,
layer 6 refuses it), and the test command is fixed by the harness rather than
chosen by the agent. Those two rules are what make running the suite acceptable
here. They are policy, not isolation.
"""

import hashlib
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path


class PathOutsideWorkspace(Exception):
    """A path resolved to somewhere outside the working copy."""


class FileTooLarge(Exception):
    """The file is bigger than the agent is allowed to handle."""


# Directories never worth copying into a working copy.
SKIP_DIRECTORIES = ("__pycache__", ".git", ".pytest_cache", ".venv", "node_modules",
                    ".mypy_cache", ".ruff_cache", ".idea", ".vscode")

# Files worth showing the agent. Anything else is treated as an asset.
SOURCE_SUFFIXES = (".py", ".txt", ".md", ".cfg", ".toml", ".ini", ".json", ".yaml", ".yml")


@dataclass
class FileSnapshot:
    """A file's content hash and size at a moment in time."""

    path: str
    sha256: str
    size: int


@dataclass
class Snapshot:
    """
    Every file in the workspace, hashed.

    Taken before the agent starts and again at the end. Comparing the two is how
    layer 8 knows whether a test file moved - and that check is the one thing
    standing between "the suite is green" and "the suite is green because the
    assertion was deleted".
    """

    files: dict = field(default_factory=dict)   # path -> FileSnapshot

    def paths(self) -> list[str]:
        names = list(self.files.keys())
        names.sort()
        return names

    def changed_against(self, other: "Snapshot") -> tuple[list[str], list[str], list[str]]:
        """Returns (modified, added, removed), each sorted."""
        modified = []
        added = []
        removed = []

        for path in self.files:
            if path not in other.files:
                added.append(path)
            elif self.files[path].sha256 != other.files[path].sha256:
                modified.append(path)

        for path in other.files:
            if path not in self.files:
                removed.append(path)

        modified.sort()
        added.sort()
        removed.sort()
        return modified, added, removed


def hash_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


class Workspace:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()

    # ---------------- creation ----------------

    @classmethod
    def create_from(cls, source_repo: str | Path, workspace_root: str | Path,
                    label: str = "task") -> "Workspace":
        """
        Copy a repository into a fresh directory.

        The directory name carries a random suffix, so two runs of the same task
        never share a workspace. Reusing one would let a previous run's edits
        look like the current run's work, which quietly makes every measurement
        afterwards wrong.
        """
        source = Path(source_repo).resolve()
        if not source.is_dir():
            raise FileNotFoundError("no repository at %s" % source)

        safe_label = ""
        for character in label:
            if character.isalnum() or character in "-_":
                safe_label = safe_label + character
        if safe_label == "":
            safe_label = "task"

        destination = Path(workspace_root).resolve() / (
            "%s_%s" % (safe_label, uuid.uuid4().hex[:8])
        )
        destination.parent.mkdir(parents=True, exist_ok=True)

        def ignore(directory, names):
            skipped = []
            for name in names:
                if name in SKIP_DIRECTORIES:
                    skipped.append(name)
            return skipped

        shutil.copytree(source, destination, ignore=ignore)
        return cls(destination)

    def remove(self) -> None:
        if self.root.exists():
            shutil.rmtree(self.root)

    # ---------------- the path guard ----------------

    def resolve(self, relative_path: str) -> Path:
        """
        Turn a path the agent named into a real path inside the workspace.

        Three things are checked, and all three have been attacked in practice:

          absolute paths     "/etc/passwd" must not simply be used as given
          traversal          "../../etc/passwd" must not climb out
          symlinks           a link inside the workspace pointing outside must
                             not be written through

        The last one is why the FULL path is resolved rather than just checking
        for ".." in the text. Path.resolve() follows symlinks, so a link that
        leaves the workspace resolves to somewhere outside it and is caught by
        the same containment test as everything else. A string check for ".."
        would pass it straight through.
        """
        if relative_path is None or str(relative_path).strip() == "":
            raise PathOutsideWorkspace("no path was given")

        candidate = Path(str(relative_path).strip())
        if candidate.is_absolute():
            # An absolute path is only acceptable if it is already inside.
            resolved = candidate.resolve()
        else:
            resolved = (self.root / candidate).resolve()

        if resolved != self.root and self.root not in resolved.parents:
            raise PathOutsideWorkspace(
                "%r resolves to %s, which is outside the workspace at %s"
                % (str(relative_path), resolved, self.root)
            )

        return resolved

    def relative(self, path: Path) -> str:
        return str(Path(path).resolve().relative_to(self.root))

    def contains(self, relative_path: str) -> bool:
        try:
            self.resolve(relative_path)
            return True
        except PathOutsideWorkspace:
            return False

    # ---------------- reading and writing ----------------

    def read(self, relative_path: str, max_bytes: int = 400000) -> str:
        path = self.resolve(relative_path)
        if not path.is_file():
            raise FileNotFoundError("no file at %s" % relative_path)

        size = path.stat().st_size
        if size > max_bytes:
            raise FileTooLarge(
                "%s is %d bytes, over the %d byte limit" % (relative_path, size, max_bytes)
            )
        return path.read_text(encoding="utf-8", errors="replace")

    def write(self, relative_path: str, content: str, max_bytes: int = 400000) -> None:
        payload = content.encode("utf-8")
        if len(payload) > max_bytes:
            raise FileTooLarge(
                "writing %d bytes to %s, over the %d byte limit"
                % (len(payload), relative_path, max_bytes)
            )
        path = self.resolve(relative_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)

    def exists(self, relative_path: str) -> bool:
        try:
            return self.resolve(relative_path).is_file()
        except PathOutsideWorkspace:
            return False

    # ---------------- listing ----------------

    def all_files(self) -> list[str]:
        """Every file in the workspace, as paths relative to its root, sorted."""
        found = []
        for path in self.root.rglob("*"):
            if not path.is_file():
                continue
            skip = False
            for part in path.parts:
                if part in SKIP_DIRECTORIES:
                    skip = True
                    break
            if skip:
                continue
            found.append(str(path.relative_to(self.root)))
        found.sort()
        return found

    def source_files(self) -> list[str]:
        found = []
        for name in self.all_files():
            if name.endswith(SOURCE_SUFFIXES):
                found.append(name)
        return found

    # ---------------- snapshots ----------------

    def snapshot(self) -> Snapshot:
        taken = Snapshot()
        for name in self.all_files():
            path = self.root / name
            try:
                payload = path.read_bytes()
            except OSError:
                continue
            taken.files[name] = FileSnapshot(
                path=name, sha256=hash_bytes(payload), size=len(payload),
            )
        return taken
