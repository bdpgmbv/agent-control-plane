"""
LAYER 4, STEP 3 - BUILDING THE PROMPT WITHOUT BLOWING THE BUDGET
================================================================
Turn the repository into something that fits in a prompt, and say what was left
out.

Three rules, in order of how much they save:

  1. the map always goes in       it is tiny and it tells the agent what exists
  2. only the chosen files        ranked by layer 4 step 2, capped by settings
  3. long files are truncated     from the middle, keeping both ends

Truncating from the middle rather than the end is deliberate. The top of a Python
file holds its imports and the bottom often holds the function somebody is asking
about; cutting the tail loses exactly the part that tends to matter. Cutting the
middle keeps both ends and says how many lines went missing, so the agent can ask
for that region specifically if it needs it.

Everything removed is announced. An agent that does not know its view is partial
will confidently conclude that something does not exist.
"""

from dataclasses import dataclass, field


@dataclass
class ContextBundle:
    text: str = ""
    files_included: list[str] = field(default_factory=list)
    files_omitted: list[str] = field(default_factory=list)
    characters: int = 0
    truncated_files: list[str] = field(default_factory=list)

    def describe(self) -> str:
        parts = ["%d characters" % self.characters,
                 "%d file(s) shown" % len(self.files_included)]
        if len(self.truncated_files) > 0:
            parts.append("%d truncated" % len(self.truncated_files))
        if len(self.files_omitted) > 0:
            parts.append("%d not shown" % len(self.files_omitted))
        return ", ".join(parts)


def truncate_middle(source: str, limit: int) -> tuple[str, bool]:
    """Keep the head and the tail, and say how much went missing in between."""
    if len(source) <= limit:
        return source, False

    keep = limit // 2
    head = source[:keep]
    tail = source[-keep:]

    removed_lines = source[keep:len(source) - keep].count("\n")
    marker = ("\n\n... %d lines omitted from the middle of this file. Ask for a "
              "specific region if you need it ...\n\n" % removed_lines)
    return head + marker + tail, True


def build_context(workspace, repo_map: str, chosen_paths: list[str],
                  max_file_characters: int,
                  all_source_paths: list[str] | None = None) -> ContextBundle:
    bundle = ContextBundle()
    pieces = []

    pieces.append("REPOSITORY MAP")
    pieces.append("=" * 60)
    pieces.append(repo_map)
    pieces.append("")

    for path in chosen_paths:
        try:
            source = workspace.read(path)
        except Exception as error:
            pieces.append("--- %s (could not be read: %s) ---" % (path, error))
            continue

        shown, was_truncated = truncate_middle(source, max_file_characters)
        if was_truncated:
            bundle.truncated_files.append(path)

        pieces.append("FILE: %s" % path)
        pieces.append("-" * 60)
        pieces.append(shown)
        pieces.append("")
        bundle.files_included.append(path)

    if all_source_paths is not None:
        for path in all_source_paths:
            if path not in bundle.files_included:
                bundle.files_omitted.append(path)

    if len(bundle.files_omitted) > 0:
        pieces.append("NOT SHOWN (they are in the map above; ask if you need one):")
        pieces.append("  " + ", ".join(bundle.files_omitted))
        pieces.append("")

    bundle.text = "\n".join(pieces)
    bundle.characters = len(bundle.text)
    return bundle
