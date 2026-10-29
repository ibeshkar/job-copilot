# What: loads the evaluation datasets in evals/datasets/ into small typed objects: labelled job
#       postings, drafting cases and chat questions.
# Why: the eval runners and the offline tests read the same files the same way, and a typo in
#      a dataset fails loudly here (Pydantic validation) instead of halfway through a paid run.
# Used by: evals/runners.py, evals/run_evals.py, evals/test_*.py, tests/test_evals.py.

import json
from pathlib import Path

from pydantic import BaseModel, Field

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATASETS_DIR = PROJECT_ROOT / "evals" / "datasets"


class LabelledRequirement(BaseModel):
    """One must-have requirement a human found in a posting, e.g. "Strong SQL"."""

    label: str
    keywords: list[str] = Field(min_length=1)  # a prediction containing any of these matches


class LabelledPosting(BaseModel):
    """A job posting with its hand-labelled must-have requirements."""

    id: str
    file: str  # path relative to the project root
    must: list[LabelledRequirement] = Field(min_length=1)

    @property
    def text(self) -> str:
        return (PROJECT_ROOT / self.file).read_text()


class DraftingJob(BaseModel):
    """The structured job fields a drafting case needs (normally made by `ingest_job`)."""

    title: str
    company: str
    location: str
    remote_policy: str
    seniority: str
    summary: str


class Gap(BaseModel):
    """A requirement the candidate doesn't fully meet, with the honest way to address it."""

    requirement: str
    status: str  # "partial" or "missing"
    honest_positioning: str


class DraftingCase(BaseModel):
    """One job plus the claims the user approved and removed at the claim check."""

    id: str
    file: str
    job: DraftingJob
    approved: list[str] = Field(min_length=1)
    removed: list[str]
    gaps: list[Gap]

    @property
    def posting_text(self) -> str:
        return (PROJECT_ROOT / self.file).read_text()


def _load(name: str) -> dict:
    return json.loads((DATASETS_DIR / name).read_text())


def load_labelled_postings() -> list[LabelledPosting]:
    """The 10 postings with their must-have labels (requirements_labels.json)."""
    return [
        LabelledPosting.model_validate(p) for p in _load("requirements_labels.json")["postings"]
    ]


def load_drafting_cases() -> list[DraftingCase]:
    """The 5 drafting cases for the demo persona (drafting_cases.json)."""
    return [DraftingCase.model_validate(c) for c in _load("drafting_cases.json")["cases"]]


def load_chat_questions() -> list[str]:
    """The chat questions for the answer relevancy metric (chat_questions.json)."""
    return list(_load("chat_questions.json")["questions"])
