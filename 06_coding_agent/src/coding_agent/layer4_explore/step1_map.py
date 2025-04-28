"""
LAYER 4, STEP 1 - THE SHAPE OF THE REPOSITORY
=============================================
A compact map: every file, and for Python files the names defined in them.

The map for the benchmark repository is about 900 characters. The repository
itself is about 14,000. That ratio is the point - the agent gets to know what
exists and where before deciding what to read, and finding out costs almost
nothing.

Definitions are read with `ast`, not with regular expressions over the source.
A regex for "^def " finds methods indented inside classes, misses decorated
functions, and matches the word def inside a docstring. The parser already knows
the answer, and when the file does not parse that is itself worth reporting -
a syntax error is usually the most important fact about a file.
"""

import ast
from dataclasses import dataclass, field


@dataclass
class FileOutline:
    path: str
    lines: int = 0
    characters: int = 0
    classes: list[str] = field(default_factory=list)
    functions: list[str] = field(default_factory=list)
    parse_error: str = ""

    def names(self) -> list[str]:
        found = []
        for name in self.classes:
            found.append(name)
        for name in self.functions:
            found.append(name)
        return found


def outline_python(path: str, source: str) -> FileOutline:
    outline = FileOutline(
        path=path,
        lines=source.count("\n") + 1,
        characters=len(source),
    )

    try:
        tree = ast.parse(source)
    except SyntaxError as error:
        outline.parse_error = "line %s: %s" % (error.lineno, error.msg)
        return outline

    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            methods = []
            for child in node.body:
                if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                    methods.append(child.name)
            if len(methods) > 0:
                outline.classes.append("%s(%s)" % (node.name, ", ".join(methods)))
            else:
                outline.classes.append(node.name)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            outline.functions.append(node.name)

    return outline


def outline_plain(path: str, source: str) -> FileOutline:
    return FileOutline(
        path=path,
        lines=source.count("\n") + 1,
        characters=len(source),
    )


def build_outlines(workspace) -> list[FileOutline]:
    outlines = []
    for name in workspace.source_files():
        try:
            source = workspace.read(name)
        except Exception:
            continue
        if name.endswith(".py"):
            outlines.append(outline_python(name, source))
        else:
            outlines.append(outline_plain(name, source))
    return outlines


def render_map(outlines: list[FileOutline]) -> str:
    """The map as the agent sees it."""
    lines = []
    for outline in outlines:
        header = "%s  (%d lines)" % (outline.path, outline.lines)
        if outline.parse_error != "":
            header = header + "   *** DOES NOT PARSE: %s ***" % outline.parse_error
        lines.append(header)

        for name in outline.classes:
            lines.append("    class %s" % name)
        for name in outline.functions:
            lines.append("    def %s" % name)

    return "\n".join(lines)
