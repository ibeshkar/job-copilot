# What: turns one evaluation run into a Markdown report. It writes the results section of
#       docs/EVALUATION.md (between two marker comments) and the raw numbers to
#       evals/results/latest.json.
# Why: `python evals/run_evals.py` should produce the report in one step (Phase 9 "done when"),
#      while the hand-written parts of EVALUATION.md (setup, failure analysis) stay untouched.
# Used by: evals/run_evals.py, tests/test_evals.py.

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from evals.runners import ChatResult, DraftResult, MetricResult
from evals.scoring import ExtractionScore, ScoreSpread, micro_average, ranking_holds

START_MARKER = "<!-- EVAL-RESULTS:START -->"
END_MARKER = "<!-- EVAL-RESULTS:END -->"

# Targets we set before the first run (see docs/EVALUATION.md, "What good looks like").
EXTRACTION_TARGET = 0.8  # precision and recall
MAX_SCORE_STDEV = 5.0  # fit-score points


@dataclass
class EvalRun:
    """Everything one run of `run_evals.py` measured. Sections that were skipped are None."""

    started_at: str
    app_model: str
    judge_model: str
    prompt_version: str
    extraction: list[ExtractionScore] | None = None
    drafting: list[DraftResult] | None = None
    chat: list[ChatResult] | None = None
    consistency: list[ScoreSpread] | None = None
    costs: dict[str, dict] = field(default_factory=dict)  # part → {"tokens": .., "cost_usd": ..}


# ---------------------------------------------------------------- helpers


def _cell(text: str, limit: int = 160) -> str:
    """Make text safe for one Markdown table cell: one line, no pipes, not too long."""
    flat = " ".join(str(text).split()).replace("|", "/")
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def _metric(result: MetricResult | None) -> str:
    if result is None:
        return "–"
    if result.score is None:
        return "error"
    return f"{result.score:.2f} {'✅' if result.passed else '❌'}"


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _scores(results: list[MetricResult | None]) -> list[float]:
    return [r.score for r in results if r is not None and r.score is not None]


def _passed(results: list[MetricResult | None]) -> str:
    scored = [r for r in results if r is not None and r.score is not None]
    return f"{sum(r.passed for r in scored)}/{len(scored)} pass"


def _fmt(value: float | None) -> str:
    return "–" if value is None else f"{value:.2f}"


# ---------------------------------------------------------------- sections


def _summary(run: EvalRun) -> list[str]:
    rows = ["| Evaluation | Metric | Result | Target |", "|---|---|---|---|"]
    if run.extraction is not None:
        p, r, f1 = micro_average(run.extraction)
        ok = "✅" if p >= EXTRACTION_TARGET and r >= EXTRACTION_TARGET else "❌"
        rows.append(
            f"| Requirements extraction ({len(run.extraction)} postings) | precision / recall / "
            f"F1 (micro) | {p:.2f} / {r:.2f} / {f1:.2f} {ok} | ≥ {EXTRACTION_TARGET} each |"
        )
    if run.drafting is not None:
        faith = [d.faithfulness for d in run.drafting]
        tailor = [d.tailoring for d in run.drafting]
        leaks = sum(d.removed_claim_leaked for d in run.drafting)
        n = len(run.drafting)
        rows += [
            f"| Drafting ({n} cases) | Faithfulness to approved claims (mean) | "
            f"{_fmt(_mean(_scores(faith)))} ({_passed(faith)}) | ≥ 0.9 per letter |",
            f"| Drafting | G-Eval: tailored, no generic filler (mean) | "
            f"{_fmt(_mean(_scores(tailor)))} ({_passed(tailor)}) | ≥ 0.6 per letter |",
            f"| Drafting | Removed claims that appear word for word | {leaks}/{n} | 0 |",
        ]
    if run.chat is not None:
        rel = [c.relevancy for c in run.chat]
        rows.append(
            f"| Copilot chat ({len(run.chat)} questions) | Answer relevancy (mean) | "
            f"{_fmt(_mean(_scores(rel)))} ({_passed(rel)}) | ≥ 0.7 per answer |"
        )
    if run.consistency is not None:
        worst = max((s.stdev for s in run.consistency), default=0.0)
        runs = max((len(s.scores) for s in run.consistency), default=0)
        ok = "✅" if worst <= MAX_SCORE_STDEV else "❌"
        rank = "✅" if ranking_holds(run.consistency) else "❌"
        rows += [
            f"| Fit score ({len(run.consistency)} jobs × {runs} runs) | Largest standard "
            f"deviation | {worst:.1f} points {ok} | ≤ {MAX_SCORE_STDEV:.0f} points |",
            f"| Fit score | Every run ranks good > partial > poor | {rank} | ✅ |",
        ]
    return rows


def _extraction(scores: list[ExtractionScore]) -> list[str]:
    rows = [
        "| Posting | Predicted must-haves | Precision | Recall | Missed labels | "
        "Extra must-haves (not in labels) |",
        "|---|---|---|---|---|---|",
    ]
    for s in scores:
        missed = [
            f"{label} (listed as nice)" if label in s.demoted_labels else label
            for label in s.missed_labels
        ]
        rows.append(
            f"| {s.posting_id} | {len(s.predicted_musts)} | {s.precision:.2f} | {s.recall:.2f} | "
            f"{_cell('; '.join(missed)) or '–'} | {_cell('; '.join(s.extra_predictions)) or '–'} |"
        )
    return rows


def _drafting(results: list[DraftResult]) -> list[str]:
    rows = [
        "| Case | Letter words | Faithfulness | Tailoring | Removed claim leaked | "
        "Judge's note (faithfulness) |",
        "|---|---|---|---|---|---|",
    ]
    for d in results:
        if d.error:
            rows.append(f"| {d.case_id} | – | – | – | – | Drafting failed: {_cell(d.error)} |")
            continue
        note = ""
        if d.faithfulness is not None:
            note = d.faithfulness.error or d.faithfulness.reason
        rows.append(
            f"| {d.case_id} | {d.words} | {_metric(d.faithfulness)} | {_metric(d.tailoring)} | "
            f"{'❌ yes' if d.removed_claim_leaked else '✅ no'} | {_cell(note, 240)} |"
        )
    return rows


def _chat(results: list[ChatResult]) -> list[str]:
    rows = ["| Question | Tools used | Relevancy | Judge's note |", "|---|---|---|---|"]
    for c in results:
        note = c.error or (c.relevancy.error or c.relevancy.reason if c.relevancy else "")
        tools = ", ".join(dict.fromkeys(c.tools_used)) or "none"
        rows.append(f"| {_cell(c.question)} | {tools} | {_metric(c.relevancy)} | {_cell(note)} |")
    return rows


def _consistency(spreads: list[ScoreSpread]) -> list[str]:
    rows = ["| Job | Scores | Mean | Std dev | Range |", "|---|---|---|---|---|"]
    for s in spreads:
        scores = ", ".join(map(str, s.scores)) or "no result"
        rows.append(f"| {s.job} | {scores} | {s.mean:.1f} | {s.stdev:.1f} | {s.range} |")
    return rows


def render_results(run: EvalRun) -> str:
    """The generated results section of docs/EVALUATION.md (without the markers)."""
    total_cost = sum(c["cost_usd"] for c in run.costs.values())
    lines = [
        f"_Generated by `python evals/run_evals.py` on {run.started_at}. App model: "
        f"`{run.app_model}`. Judge model: `{run.judge_model}`. Prompt version: "
        f"`{run.prompt_version}`. Cost of the run: ${total_cost:.4f}._",
        "",
        "### Summary",
        *_summary(run),
    ]
    sections = [
        ("Requirements extraction, per posting", run.extraction, _extraction),
        ("Drafting, per case", run.drafting, _drafting),
        ("Copilot chat, per question", run.chat, _chat),
        ("Fit-score consistency", run.consistency, _consistency),
    ]
    for title, data, render in sections:
        if data is not None:
            lines += ["", f"### {title}", *render(data)]
    if run.costs:
        lines += ["", "### Cost of this run", "| Part | Tokens | Cost (USD) |", "|---|---|---|"]
        for part, cost in run.costs.items():
            lines.append(f"| {part} | {cost['tokens']:,} | ${cost['cost_usd']:.4f} |")
    return "\n".join(lines)


def write_report(run: EvalRun, doc_path: Path, json_path: Path) -> None:
    """Replace the results section of the evaluation doc and save the raw results as JSON.

    If the doc has no markers yet, the section is appended at the end.
    """
    section = f"{START_MARKER}\n{render_results(run)}\n{END_MARKER}"
    doc = doc_path.read_text() if doc_path.exists() else "# Evaluation\n"
    if START_MARKER in doc and END_MARKER in doc:
        before = doc.split(START_MARKER, 1)[0]
        after = doc.split(END_MARKER, 1)[1]
        doc = f"{before}{section}{after}"
    else:
        doc = f"{doc.rstrip()}\n\n{section}\n"
    doc_path.write_text(doc)

    json_path.parent.mkdir(parents=True, exist_ok=True)
    data = asdict(run)
    if run.extraction is not None:
        data["extraction_micro"] = dict(
            zip(("precision", "recall", "f1"), micro_average(run.extraction), strict=True)
        )
    json_path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
