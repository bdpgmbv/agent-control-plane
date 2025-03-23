"""
LAYER 7 - API: DOCUMENT MANAGEMENT
==================================
Adding, listing and removing documents. Every write needs an admin key.

Note what the ingest endpoints return: how many chunks were created AND how many
were rejected as duplicates. Silence about the duplicates would hide the most
common cause of a knowledge base slowly getting worse.
"""

import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status

from rag_assistant.layer1_config.settings import ApiKeyRecord, find_beside_project
from rag_assistant.layer2_models.schemas import DocumentSummary, IngestResponse, IngestTextRequest
from rag_assistant.layer3_storage.factory import get_store
from rag_assistant.layer4_ingestion.step1_load import UnsupportedFileError
from rag_assistant.layer7_api.dependencies import get_ingestion
from rag_assistant.layer7_api.security import require_admin, require_caller

router = APIRouter()

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SAMPLES_FOLDER = find_beside_project("samples")

# Which access tag each sample file gets, so the demo shows RBAC working.
SAMPLE_ACCESS_TAGS = {
    "refund_policy.md": "public",
    "shipping_policy.md": "public",
    "security_faq.md": "internal",
    "salary_bands.md": "secret",
}


def check_tag_is_allowed(caller: ApiKeyRecord, access_tag: str) -> None:
    """
    You may not publish into a tag you cannot read.

    Without this check a "user" key could write a document tagged "secret" and
    then be unable to see what it had created - and worse, could use it to smuggle
    content into an admin's answers.
    """
    if access_tag not in caller.allowed_tags:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your key may not write documents tagged '%s'. Allowed: %s"
            % (access_tag, ", ".join(caller.allowed_tags)),
        )


@router.get("/api/documents", response_model=list[DocumentSummary])
async def list_documents(caller: ApiKeyRecord = Depends(require_caller)) -> list[DocumentSummary]:
    """Every document this key is allowed to see."""
    return get_store().list_documents(caller.allowed_tags)


@router.post("/api/documents/text", response_model=IngestResponse)
async def ingest_text(
    request: IngestTextRequest,
    caller: ApiKeyRecord = Depends(require_admin),
) -> IngestResponse:
    """Add a document by pasting its text."""
    check_tag_is_allowed(caller, request.access_tag)

    if request.text.strip() == "":
        raise HTTPException(status_code=400, detail="The document text is empty.")

    try:
        return get_ingestion().ingest_text(
            title=request.title,
            text=request.text,
            source=request.source,
            access_tag=request.access_tag,
            extra_metadata=request.metadata,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.post("/api/documents/upload", response_model=IngestResponse)
async def upload_document(
    file: UploadFile = File(...),
    access_tag: str = Form(default="public"),
    caller: ApiKeyRecord = Depends(require_admin),
) -> IngestResponse:
    """Add a document by uploading a .txt, .md, .pdf or .html file."""
    check_tag_is_allowed(caller, access_tag)

    if file.filename is None or file.filename.strip() == "":
        raise HTTPException(status_code=400, detail="No file name was provided.")

    # Write the upload to a temporary file so the loaders can work with a path.
    suffix = Path(file.filename).suffix
    temporary = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    try:
        shutil.copyfileobj(file.file, temporary)
        temporary.close()

        temporary_path = Path(temporary.name)
        # Keep the real file name so citations show something recognisable.
        named_path = temporary_path.parent / file.filename
        temporary_path.rename(named_path)

        return get_ingestion().ingest_file(named_path, access_tag=access_tag)

    except UnsupportedFileError as error:
        raise HTTPException(status_code=415, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    finally:
        for candidate in [Path(temporary.name), Path(temporary.name).parent / file.filename]:
            if candidate.exists():
                candidate.unlink()


@router.post("/api/documents/seed")
async def seed_sample_documents(caller: ApiKeyRecord = Depends(require_admin)) -> dict:
    """
    Load the four sample documents shipped with the project.

    They are tagged public / internal / secret on purpose, so you can log in with
    the user key and watch the secret one disappear from the answers.
    """
    if not SAMPLES_FOLDER.exists():
        raise HTTPException(status_code=404, detail="The samples folder is missing.")

    results: list[dict] = []
    for path in sorted(SAMPLES_FOLDER.iterdir()):
        if not path.is_file():
            continue
        if path.suffix.lower() not in [".md", ".txt", ".pdf", ".html"]:
            continue

        access_tag = SAMPLE_ACCESS_TAGS.get(path.name, "public")
        if access_tag not in caller.allowed_tags:
            continue

        outcome = get_ingestion().ingest_file(path, access_tag=access_tag)
        results.append(
            {
                "title": outcome.title,
                "access_tag": access_tag,
                "chunks_created": outcome.chunks_created,
                "chunks_skipped_as_duplicate": outcome.chunks_skipped_as_duplicate,
            }
        )

    return {"documents": results, "chunks_indexed": get_store().count_chunks()}


@router.delete("/api/documents/{document_id}")
async def delete_document(
    document_id: str,
    caller: ApiKeyRecord = Depends(require_admin),
) -> dict:
    """Remove a document and every chunk that came from it."""
    visible_ids: list[str] = []
    for summary in get_store().list_documents(caller.allowed_tags):
        visible_ids.append(summary.document_id)

    # Do not confirm or deny the existence of a document this key cannot see.
    if document_id not in visible_ids:
        raise HTTPException(status_code=404, detail="No such document.")

    removed = get_store().delete_document(document_id)
    return {"document_id": document_id, "chunks_removed": removed}


@router.post("/api/documents/reset")
async def reset_everything(caller: ApiKeyRecord = Depends(require_admin)) -> dict:
    """Empty the knowledge base. Only offered because this is a learning project."""
    if not caller.may_write():
        raise HTTPException(status_code=403, detail="Admin key required.")
    get_store().reset()
    return {"status": "emptied", "chunks_indexed": get_store().count_chunks()}
