"""
LAYER 3, STEP 1 - GET TEXT OUT OF A FILE
========================================
Three kinds of input, one output shape.

  .txt / .md   the text is already there
  .pdf         pypdf pulls the text layer out
  .png / .jpg  there is no text layer, so a vision model has to read it

The third case is the only one that needs a model, and it is worth noticing why:
a PDF with a text layer gives perfect characters for free. Sending it to a vision
model instead would cost money to get a worse answer. Cheap and exact beats
clever whenever it is available.
"""

import base64
import hashlib
from dataclasses import dataclass, field
from pathlib import Path

TEXT_SUFFIXES = (".txt", ".md", ".text")
PDF_SUFFIXES = (".pdf",)
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".tif", ".tiff")


class UnsupportedDocument(Exception):
    """The file is not something this pipeline can read."""


@dataclass
class LoadedDocument:
    document_id: str
    filename: str
    kind: str                      # "text" | "pdf" | "image"
    text: str = ""
    image_base64: str = ""         # only set for images, for the vision step
    image_media_type: str = ""
    page_count: int = 0
    notes: list[str] = field(default_factory=list)

    def needs_vision(self) -> bool:
        return self.kind == "image" and self.text.strip() == ""


def make_document_id(filename: str, payload: bytes) -> str:
    """
    The id is a hash of the content, not a counter.

    That means uploading the same file twice produces the same id, which is how
    the duplicate check in layer 6 can recognise a file it has already seen even
    when someone renames it.
    """
    digest = hashlib.sha256(payload).hexdigest()
    return "doc_" + digest[:16]


def classify_suffix(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix in TEXT_SUFFIXES:
        return "text"
    if suffix in PDF_SUFFIXES:
        return "pdf"
    if suffix in IMAGE_SUFFIXES:
        return "image"
    raise UnsupportedDocument(
        "cannot read '%s'. Supported: text, pdf, image (%s)"
        % (filename, ", ".join(TEXT_SUFFIXES + PDF_SUFFIXES + IMAGE_SUFFIXES))
    )


def media_type_for(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix in (".jpg", ".jpeg"):
        return "image/jpeg"
    if suffix == ".png":
        return "image/png"
    if suffix == ".webp":
        return "image/webp"
    if suffix == ".gif":
        return "image/gif"
    return "image/png"


def read_pdf_text(payload: bytes) -> tuple[str, int, list[str]]:
    """
    Pull the text layer out of a PDF.

    A PDF that was produced by scanning has no text layer, so this returns
    almost nothing. That is not a failure to hide - the caller is told, so the
    document can be sent to the vision path instead of quietly processed as
    an empty invoice.
    """
    import io

    from pypdf import PdfReader

    notes: list[str] = []
    reader = PdfReader(io.BytesIO(payload))
    pages: list[str] = []
    for page in reader.pages:
        extracted = page.extract_text()
        if extracted is None:
            extracted = ""
        pages.append(extracted)

    text = "\n".join(pages)
    if len(text.strip()) < 20:
        notes.append(
            "this PDF has little or no text layer, which usually means it is a "
            "scan. It needs to be read as an image."
        )
    return text, len(pages), notes


def load_from_bytes(filename: str, payload: bytes) -> LoadedDocument:
    kind = classify_suffix(filename)
    document_id = make_document_id(filename, payload)

    if kind == "text":
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError:
            # Latin-1 never fails, so a stray byte cannot take down the upload.
            text = payload.decode("latin-1")
            return LoadedDocument(
                document_id=document_id,
                filename=filename,
                kind="text",
                text=text,
                page_count=1,
                notes=["file was not valid UTF-8, read as latin-1"],
            )
        return LoadedDocument(
            document_id=document_id,
            filename=filename,
            kind="text",
            text=text,
            page_count=1,
        )

    if kind == "pdf":
        text, page_count, notes = read_pdf_text(payload)
        return LoadedDocument(
            document_id=document_id,
            filename=filename,
            kind="pdf",
            text=text,
            page_count=page_count,
            notes=notes,
        )

    return LoadedDocument(
        document_id=document_id,
        filename=filename,
        kind="image",
        text="",
        image_base64=base64.b64encode(payload).decode("ascii"),
        image_media_type=media_type_for(filename),
        page_count=1,
        notes=["image has no text layer, a vision model must read it"],
    )


def load_from_path(path: str | Path) -> LoadedDocument:
    path = Path(path)
    payload = path.read_bytes()
    return load_from_bytes(path.name, payload)


def load_from_text(text: str, filename: str = "pasted.txt") -> LoadedDocument:
    payload = text.encode("utf-8")
    return LoadedDocument(
        document_id=make_document_id(filename, payload),
        filename=filename,
        kind="text",
        text=text,
        page_count=1,
    )
