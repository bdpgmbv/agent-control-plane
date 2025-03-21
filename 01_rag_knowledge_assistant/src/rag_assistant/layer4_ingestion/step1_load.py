"""
LAYER 4 - INGESTION, STEP 1: LOAD
=================================
Turn a file or a URL into plain text plus a title.

Supported today: .txt, .md, .pdf, .html/.htm
Everything else is rejected with a clear message rather than silently producing
garbage - a wrong answer traced back to a badly parsed file is expensive to debug.
"""

from pathlib import Path

SUPPORTED_EXTENSIONS = [".txt", ".md", ".markdown", ".pdf", ".html", ".htm"]


class LoadedFile:
    """What a loader returns."""

    def __init__(self, title: str, text: str, source: str, page_count: int = 0) -> None:
        self.title = title
        self.text = text
        self.source = source
        self.page_count = page_count


class UnsupportedFileError(Exception):
    """Raised when we do not know how to read a file."""


def load_plain_text(path: Path) -> LoadedFile:
    text = path.read_text(encoding="utf-8", errors="replace")
    return LoadedFile(title=path.stem, text=text, source=path.name)


def load_pdf(path: Path) -> LoadedFile:
    from pypdf import PdfReader

    reader = PdfReader(str(path))

    pages: list[str] = []
    page_number = 0
    for page in reader.pages:
        page_number = page_number + 1
        extracted = page.extract_text()
        if extracted is None:
            extracted = ""
        # Keep the page number in the text so citations can mention it.
        pages.append(f"[page {page_number}]\n{extracted}")

    title = path.stem
    if reader.metadata is not None:
        metadata_title = reader.metadata.get("/Title")
        if metadata_title is not None and str(metadata_title).strip() != "":
            title = str(metadata_title).strip()

    return LoadedFile(
        title=title,
        text="\n\n".join(pages),
        source=path.name,
        page_count=page_number,
    )


def load_html(path: Path) -> LoadedFile:
    from bs4 import BeautifulSoup

    raw = path.read_text(encoding="utf-8", errors="replace")
    soup = BeautifulSoup(raw, "html.parser")

    # Scripts and styles are not content.
    for tag_name in ["script", "style", "nav", "footer"]:
        for tag in soup.find_all(tag_name):
            tag.decompose()

    title = path.stem
    if soup.title is not None and soup.title.string is not None:
        cleaned_title = soup.title.string.strip()
        if cleaned_title != "":
            title = cleaned_title

    return LoadedFile(title=title, text=soup.get_text("\n"), source=path.name)


def load_file(path: Path) -> LoadedFile:
    """Pick the right loader for a file based on its extension."""
    extension = path.suffix.lower()

    if extension not in SUPPORTED_EXTENSIONS:
        supported = ", ".join(SUPPORTED_EXTENSIONS)
        raise UnsupportedFileError(
            f"Cannot read '{path.name}'. Supported file types are: {supported}"
        )

    if extension == ".pdf":
        return load_pdf(path)
    if extension in (".html", ".htm"):
        return load_html(path)
    return load_plain_text(path)


def load_from_text(title: str, text: str, source: str = "pasted-text") -> LoadedFile:
    """Used when the UI pastes text directly instead of uploading a file."""
    return LoadedFile(title=title, text=text, source=source)
