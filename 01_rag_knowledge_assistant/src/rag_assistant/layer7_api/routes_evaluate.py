"""
LAYER 7 - API: RUNNING THE EVALUATION SUITE
===========================================
Exposes layer 8 over HTTP so the UI has a "Run evaluation" button.

It is an admin action: it runs every question in the golden dataset, which costs
money when a real model is configured.
"""

from fastapi import APIRouter, Depends

from rag_assistant.layer1_config.settings import ApiKeyRecord, settings
from rag_assistant.layer7_api.security import require_admin
from rag_assistant.layer8_evaluation.run_eval import load_dataset, run_evaluation

router = APIRouter()


@router.get("/api/evaluation/dataset")
async def read_dataset() -> dict:
    """The golden dataset, so the UI can show what is being tested."""
    return load_dataset()


@router.post("/api/evaluation/run")
async def run(
    use_judge: bool = False,
    caller: ApiKeyRecord = Depends(require_admin),
) -> dict:
    """
    Run the whole suite.

    use_judge adds a model-graded second opinion. It is ignored when no API key is
    configured, because a made-up score is worse than no score.
    """
    if use_judge and not settings.using_real_llm():
        use_judge = False

    return run_evaluation(use_judge=use_judge)
