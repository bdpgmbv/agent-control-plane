"""
Rebuild the whole index with the embedder currently configured.

    python scripts/reindex.py

WHEN YOU NEED THIS
    Any time the embedding model changes: switching EMBEDDING_PROVIDER between
    offline and openai, changing EMBEDDING_MODEL, or changing
    EMBEDDING_DIMENSIONS. Old vectors cannot be compared with new ones.

It re-reads the original text from the documents table, so nothing is lost -
only the vectors are recomputed.
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from rag_assistant.layer0_shared.embeddings import build_embedder  # noqa: E402
from rag_assistant.layer1_config.settings import settings  # noqa: E402
from rag_assistant.layer3_storage.factory import build_store  # noqa: E402
from rag_assistant.layer3_storage.index_guard import fingerprint_of  # noqa: E402
from rag_assistant.layer4_ingestion.step1_load import load_from_text  # noqa: E402
from rag_assistant.layer4_ingestion.step4_pipeline import IngestionPipeline  # noqa: E402


def main() -> None:
    store = build_store()
    embedder = build_embedder()

    stored_fingerprint = store.read_index_fingerprint()
    new_fingerprint = fingerprint_of(embedder)

    print("index was built with : %s" % (stored_fingerprint or "(nothing indexed yet)"))
    print("now configured to use: %s" % new_fingerprint)
    print("live embeddings      : %s" % settings.using_real_embeddings())
    print("")

    if stored_fingerprint == new_fingerprint and store.count_chunks() > 0:
        print("Already up to date. Nothing to do.")
        return

    # Read every document's original text back out before we clear anything.
    saved: list[tuple] = []
    for summary in store.list_documents(["public", "internal", "secret"]):
        text = store.read_document_text(summary.document_id)
        saved.append((summary.title, summary.source, text, summary.access_tag))

    print("re-indexing %d documents..." % len(saved))
    store.reset()
    store.write_index_fingerprint(new_fingerprint)

    pipeline = IngestionPipeline(store=store, embedder=embedder)
    for title, source, text, access_tag in saved:
        if text.strip() == "":
            print("  SKIPPED %-22s (original text not retained by this backend)" % title)
            continue
        loaded = load_from_text(title=title, text=text, source=source)
        result = pipeline.ingest_loaded_file(loaded, access_tag=access_tag)
        print("  %-24s tag=%-9s chunks=%d" % (result.title, access_tag, result.chunks_created))

    print("")
    print("done. %d chunks indexed with %s" % (store.count_chunks(), new_fingerprint))


if __name__ == "__main__":
    main()
