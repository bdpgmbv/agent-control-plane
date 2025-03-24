"""
Load the sample documents into the knowledge base.

    python scripts/seed_demo.py

The access tags are deliberately mixed so you can see role-based access control
working: ask about salaries with the user key and you get nothing, ask with the
admin key and you get an answer.
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from rag_assistant.layer0_shared.embeddings import build_embedder  # noqa: E402
from rag_assistant.layer1_config.settings import settings  # noqa: E402
from rag_assistant.layer3_storage.factory import build_store  # noqa: E402
from rag_assistant.layer4_ingestion.step4_pipeline import IngestionPipeline  # noqa: E402

ACCESS_TAGS = {
    "refund_policy.md": "public",
    "shipping_policy.md": "public",
    "security_faq.md": "internal",
    "salary_bands.md": "secret",
}


def main() -> None:
    store = build_store()
    pipeline = IngestionPipeline(store=store, embedder=build_embedder())

    print("storage backend : %s" % store.name)
    print("embeddings      : %s (live: %s)" % (settings.embedding_provider, settings.using_real_embeddings()))
    print("")

    samples_folder = PROJECT_ROOT / "samples"
    for path in sorted(samples_folder.iterdir()):
        if path.name not in ACCESS_TAGS:
            continue

        access_tag = ACCESS_TAGS[path.name]
        result = pipeline.ingest_file(path, access_tag=access_tag)
        print(
            "%-24s tag=%-9s stored=%-3d skipped_as_duplicate=%d"
            % (result.title, access_tag, result.chunks_created, result.chunks_skipped_as_duplicate)
        )

    print("")
    print("total chunks indexed: %d" % store.count_chunks())


if __name__ == "__main__":
    main()
