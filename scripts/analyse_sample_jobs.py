# What: runs the application graph on the 3 sample job postings and prints each fit report.
# Why: the Phase 3 "done when" check: scores should rank good > partial > poor, and every run
#      should pause at the fit review (the human-in-the-loop step).
# Used by: developers, from the command line:
#     uv run python scripts/analyse_sample_jobs.py              # isolated demo data
#     uv run python scripts/analyse_sample_jobs.py --my-data    # your own profile and documents

import argparse
import os
import sys
import tempfile
import uuid
from pathlib import Path

JOBS_DIR = Path(__file__).resolve().parent.parent / "data" / "samples" / "jobs"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--my-data",
        action="store_true",
        help="use your real profile and knowledge base in data/runtime/ instead of the demo",
    )
    args = parser.parse_args()

    if not args.my_data:
        # Must happen before importing `app`, so settings point at a throwaway folder.
        os.environ["RUNTIME_DIR"] = tempfile.mkdtemp(prefix="job-copilot-demo-")

    from app.agent.application_graph import get_application_graph, thread_config
    from app.db.repo import init_db
    from app.memory.store import get_profile, save_preferences, save_profile
    from app.services.onboarding import DEMO_PREFERENCES, DEMO_PROFILE, load_demo_documents

    init_db()
    if not args.my_data:
        print("Loading the demo persona (Alex Novak) into a temporary knowledge base…")
        save_profile(DEMO_PROFILE)
        save_preferences(DEMO_PREFERENCES)
        report = load_demo_documents()
        if report.errors:
            print("Could not load demo documents:", report.errors)
            return 1
    profile = get_profile()
    print(f"Profile: {profile.name if profile else '(none)'}\n")

    graph = get_application_graph()
    results = []
    for path in sorted(JOBS_DIR.glob("*.md")):
        config = thread_config(f"script-{uuid.uuid4().hex[:8]}")
        print(f"=== {path.name}")
        for update in graph.stream({"raw_input": path.read_text()}, config, stream_mode="updates"):
            for node in update:
                print(f"  ✓ {node}")
        state = graph.get_state(config)
        values = state.values
        if values.get("errors"):
            print("  stopped:", values["errors"])
            continue

        fit = values["fit"]
        paused = state.next == ("fit_review",)
        print(f"  {values['job'].title} @ {values['job'].company}")
        print(f"  score: {fit.score}/100   paused at fit review: {paused}")
        print(f"  RAG rewrites used: {values.get('rag_iterations', 0)}")
        for flag in fit.red_flags:
            print(f"  {'⛔' if flag.blocking else '⚠️'}  {flag.message}")
        icons = {"met": "✅", "partial": "⚠️", "missing": "❌"}
        requirements = {r.id: r for r in values["requirements"]}
        for m in fit.matches:
            r = requirements[m.requirement_id]
            sources = ", ".join(sorted({e.doc_name for e in m.evidence})) or "-"
            print(f"   {icons[m.status]} [{r.importance}] {r.text}  ← {sources}")
        for warning in values.get("warnings", []):
            print("  warning:", warning)
        print()
        results.append((path.name, fit.score, paused))

    print("Summary:")
    for name, score, paused in results:
        print(f"  {score:>3}  {name}  (paused: {paused})")
    scores = [score for _, score, _ in results]
    ok = len(scores) == 3 and scores[0] > scores[1] > scores[2] and all(p for *_, p in results)
    print("\nRanking good > partial > poor and all paused:", "YES" if ok else "NO")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
