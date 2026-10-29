# What: the plain-Python eval maths: precision and recall of the extracted must-have
#       requirements against the labels, and the spread of fit scores over repeated runs.
# Why: these checks need no LLM judge, so they are cheap, exact and repeatable. Keeping them
#      pure (no I/O) also means they have their own offline unit tests.
# Used by: evals/runners.py, tests/test_evals.py.

import statistics
from dataclasses import dataclass, field

from evals.data import LabelledRequirement

# ---------------------------------------------------------------- requirement extraction


def matches_label(text: str, label: LabelledRequirement) -> bool:
    """True if the predicted requirement text contains any of the label's keywords.

    Example: "Strong SQL skills" matches the label "Advanced SQL" (keyword "sql").
    The text is padded with spaces so keywords like " r " can match a word at the edges.
    """
    padded = f" {text.lower()} "
    return any(keyword.lower() in padded for keyword in label.keywords)


@dataclass
class ExtractionScore:
    """How well the extracted must-haves of one posting match the labels."""

    posting_id: str
    predicted_musts: list[str]
    true_positives: int  # predicted must-haves that match some label
    found_labels: list[str]  # labels matched by at least one predicted must-have
    missed_labels: list[str]  # labels no predicted must-have matched
    extra_predictions: list[str]  # predicted must-haves that match no label
    demoted_labels: list[str] = field(default_factory=list)  # found only as "nice to have"

    @property
    def precision(self) -> float:
        """Share of predicted must-haves that are real must-haves (1.0 if none predicted)."""
        return self.true_positives / len(self.predicted_musts) if self.predicted_musts else 1.0

    @property
    def recall(self) -> float:
        """Share of labelled must-haves the extractor found."""
        total = len(self.found_labels) + len(self.missed_labels)
        return len(self.found_labels) / total if total else 1.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if p + r else 0.0


def score_extraction(
    posting_id: str,
    labels: list[LabelledRequirement],
    predicted_musts: list[str],
    predicted_nices: list[str] | None = None,
) -> ExtractionScore:
    """Compare the predicted must-have requirements of one posting with its labels.

    Several predictions may match one label: the extractor often splits "SQL and Python" into
    two items, and both are correct. A label found only among the nice-to-haves counts as
    missed, and is also listed in `demoted_labels`, because the importance was wrong.
    """
    found = [lab.label for lab in labels if any(matches_label(p, lab) for p in predicted_musts)]
    missed = [lab for lab in labels if lab.label not in found]
    extra = [p for p in predicted_musts if not any(matches_label(p, lab) for lab in labels)]
    demoted = [
        lab.label for lab in missed if any(matches_label(p, lab) for p in predicted_nices or [])
    ]
    return ExtractionScore(
        posting_id=posting_id,
        predicted_musts=list(predicted_musts),
        true_positives=len(predicted_musts) - len(extra),
        found_labels=found,
        missed_labels=[lab.label for lab in missed],
        extra_predictions=extra,
        demoted_labels=demoted,
    )


def micro_average(scores: list[ExtractionScore]) -> tuple[float, float, float]:
    """Precision, recall and F1 over all postings together (each requirement counts once)."""
    predicted = sum(len(s.predicted_musts) for s in scores)
    true_positives = sum(s.true_positives for s in scores)
    labels = sum(len(s.found_labels) + len(s.missed_labels) for s in scores)
    found = sum(len(s.found_labels) for s in scores)
    precision = true_positives / predicted if predicted else 1.0
    recall = found / labels if labels else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return precision, recall, f1


# ---------------------------------------------------------------- fit-score consistency


@dataclass
class ScoreSpread:
    """The fit scores of one job over several identical runs."""

    job: str
    scores: list[int]

    @property
    def mean(self) -> float:
        return statistics.fmean(self.scores) if self.scores else 0.0

    @property
    def stdev(self) -> float:
        """Population standard deviation: 0 means every run gave the same score."""
        return statistics.pstdev(self.scores) if len(self.scores) > 1 else 0.0

    @property
    def variance(self) -> float:
        return self.stdev**2

    @property
    def range(self) -> int:
        return max(self.scores) - min(self.scores) if self.scores else 0


def ranking_holds(spreads: list[ScoreSpread]) -> bool:
    """True if every run of an earlier job scored above every run of the later jobs.

    The 3 sample jobs are ordered good → partial → poor, so the worst run of the good fit must
    beat the best run of the partial fit, and so on. Stricter than comparing averages.
    """
    for better, worse in zip(spreads, spreads[1:], strict=False):
        if not better.scores or not worse.scores or min(better.scores) <= max(worse.scores):
            return False
    return True
