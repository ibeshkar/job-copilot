# What: the evaluation entry point. Loads the demo persona into a throwaway runtime folder,
#       runs the four evaluations (requirements extraction, drafting, chat, fit-score
#       consistency) and writes the report into docs/EVALUATION.md and evals/results/latest.json.
# Why: the Phase 9 "done when": one command produces the evaluation report. It uses real API
#      calls (about $0.05-0.10 per full run with gpt-4o-mini), so it is not part of `pytest`.
# Used by: developers, from the project root:
#     uv run python evals/run_evals.py                      # everything
#     uv run python evals/run_evals.py --only extraction    # one part (repeat --only for more)
#     uv run python evals/run_evals.py --runs 5 --judge-model openai/gpt-4o

import argparse
import os
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PARTS = ("extraction", "drafting", "chat", "consistency")
DEFAULT_JUDGE = "openai/gpt-4.1-mini"  # a different, stronger model than the app's default


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Job Copilot evaluations.")
    parser.add_argument("--only", action="append", choices=PARTS, help="run only this part")
    parser.add_argument("--runs", type=int, default=3, help="fit-score runs per job (default 3)")
    parser.add_argument("--judge-model", default=DEFAULT_JUDGE, help="OpenRouter model id")
    parser.add_argument(
        "--no-write", action="store_true", help="print the report instead of saving it"
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    parts = args.only or list(PARTS)

    # Must happen before importing `app`: settings, databases and Chroma go to a temp folder,
    # so the evaluation never touches the user's real data in data/runtime/.
    os.environ["RUNTIME_DIR"] = tempfile.mkdtemp(prefix="job-copilot-evals-")
    os.environ["STARTUP_KEY_CHECK"] = "false"
    os.environ["DEEPEVAL_TELEMETRY_OPT_OUT"] = "YES"  # don't send usage stats to DeepEval
    sys.path.insert(0, str(ROOT))  # so `import evals...` works when run as a script

    from app.agent.llm import get_llm
    from app.agent.prompts import PROMPT_VERSION
    from app.config import get_settings
    from app.db.repo import init_db
    from app.memory.store import save_preferences, save_profile
    from app.services.observability import configure_tracing
    from app.services.onboarding import DEMO_PREFERENCES, DEMO_PROFILE, load_demo_documents
    from app.services.usage import track_usage
    from app.tools.registry import default_toggles
    from evals import runners
    from evals.data import load_chat_questions, load_drafting_cases, load_labelled_postings
    from evals.judge import make_judge, make_metrics
    from evals.report import EvalRun, render_results, write_report

    settings = get_settings()
    if not settings.has_llm_key:
        print("OPENROUTER_API_KEY is not set in .env; the evaluations need the real API.")
        return 1
    init_db()
    configure_tracing(settings)  # LangSmith traces get the "eval_*" action tags if tracing is on

    run = EvalRun(
        started_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        app_model=settings.default_model,
        judge_model=args.judge_model,
        prompt_version=PROMPT_VERSION,
    )

    def progress(message: str) -> None:
        print(f"  … {message}", flush=True)

    @contextmanager
    def tracked(part: str) -> Iterator[None]:
        """Count the tokens of one part of the run (like the app does) into `run.costs`."""
        usage = None
        try:
            with track_usage(f"eval_{part}") as usage:
                yield
        finally:
            if usage is not None:
                run.costs[part] = {
                    "tokens": usage.total_tokens,
                    "cost_usd": round(usage.cost_usd, 6),
                }

    app_llm = get_llm(settings)
    judge = None
    if {"drafting", "chat"} & set(parts):
        judge_llm = get_llm(settings, model=args.judge_model, temperature=0.0, max_tokens=4000)
        judge = make_judge(judge_llm, args.judge_model)
        metrics = make_metrics(judge)

    if {"chat", "consistency"} & set(parts):
        print("Loading the demo persona (Alex Novak) into a temporary knowledge base…")
        save_profile(DEMO_PROFILE)
        save_preferences(DEMO_PREFERENCES)
        report = load_demo_documents()
        if report.errors:
            print("Could not load the demo documents:", report.errors)
            return 1

    if "extraction" in parts:
        print("1/4 Requirements extraction")
        with tracked("extraction (app)"):
            run.extraction = runners.run_extraction(app_llm, load_labelled_postings(), progress)

    if "drafting" in parts:
        print("2/4 Drafting")
        cases = load_drafting_cases()
        with tracked("drafting (app)"):
            drafts = []
            for case in cases:
                progress(f"drafting: {case.id}")
                drafts.append(runners.write_drafts(app_llm, case, DEMO_PROFILE, DEMO_PREFERENCES))
        with tracked("drafting (judge)"):
            for case, result in zip(cases, drafts, strict=True):
                progress(f"judging: {case.id}")
                runners.judge_drafts(result, case, DEMO_PROFILE, metrics)
        run.drafting = drafts

    if "chat" in parts:
        print("3/4 Copilot chat")
        # Only tools that need no extra API keys, so every machine gets the same setup.
        toggles = [t for t in default_toggles(settings) if t in ("experience", "tracker")]
        with tracked("chat (app)"):
            answers = []
            for question in load_chat_questions():
                progress(f"asking: {question}")
                answers.append(runners.ask_copilot(question, toggles))
        with tracked("chat (judge)"):
            for answer in answers:
                runners.judge_chat(answer, metrics)
        run.chat = answers

    if "consistency" in parts:
        print(f"4/4 Fit-score consistency ({args.runs} runs per job)")
        jobs = sorted((ROOT / "data" / "samples" / "jobs").glob("*.md"))
        with tracked("fit consistency (app)"):
            run.consistency = runners.run_fit_consistency(jobs, args.runs, progress)

    if args.no_write:
        print(render_results(run))
    else:
        doc = ROOT / "docs" / "EVALUATION.md"
        write_report(run, doc, ROOT / "evals" / "results" / "latest.json")
        print(f"\nReport written to {doc.relative_to(ROOT)} and evals/results/latest.json")
    total = sum(c["cost_usd"] for c in run.costs.values())
    print(f"Total cost of this run: ${total:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
