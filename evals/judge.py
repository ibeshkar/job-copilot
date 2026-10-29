# What: `OpenRouterJudge`, a DeepEval model class that sends DeepEval's judge prompts through
#       our own `get_llm()` factory (OpenRouter), plus `make_metrics()` with the three DeepEval
#       metrics we use: faithfulness, answer relevancy and a G-Eval rubric.
# Why: DeepEval calls OpenAI directly by default, but this project only has an OpenRouter key
#      (DECISIONS.md D7). Wrapping our factory also means judge calls are counted by the same
#      token/cost tracking as the app's own calls.
# Used by: evals/runners.py, tests/test_evals.py (with a fake chat model).

import asyncio
from typing import Any

from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel

from app.utils.logging import get_logger

logger = get_logger(__name__)

# Thresholds a case must reach to "pass". Faithfulness is strict on purpose: a cover letter
# that states one unbacked fact about the candidate is a real problem.
FAITHFULNESS_THRESHOLD = 0.9
RELEVANCY_THRESHOLD = 0.7
TAILORING_THRESHOLD = 0.6

TAILORING_STEPS = [
    "Read the job posting in the input and list its specific needs: tasks, skills, domain.",
    "Check whether the cover letter in the actual output refers to those specific needs, the "
    "company or its product, instead of statements that would fit any job.",
    "Penalise generic filler such as 'I am a hard-working team player', 'I am passionate about "
    "technology' or 'your esteemed company', and sentences that add no information.",
    # A gap honestly named (or simply not claimed) is not a failure: the app must never
    # pretend the candidate has a skill they lack. The first rubric version penalised that.
    "Check that the letter connects the candidate's real experience to the most important "
    "requirements. Do NOT penalise the letter for not claiming skills the candidate lacks, or "
    "for naming a gap honestly; penalise only if it ignores requirements it could address.",
    "Give a high score only if most paragraphs could not be reused unchanged for another job.",
]


# Used instead of DeepEval's default claim extraction for faithfulness (see `make_metrics`).
# Technique: a narrow task + explicit include/skip lists + JSON output. Lives here, not in
# app/agent/prompts.py, because it belongs to the evaluation, not to the app.
CANDIDATE_CLAIMS_PROMPT = """You check cover letters for invented experience.
Extract every FACTUAL statement the cover letter makes about the candidate: their experience,
projects, skills, tools, achievements and numbers, jobs, education, certificates, languages,
location and work authorisation. Rewrite each one as a short, self-contained sentence.

Skip, because they cannot be checked against documents:
- opinions, motivation, enthusiasm and gratitude ("I am excited...", "I believe...", "thank you")
- statements about the company, the team or the role
- greetings, sign-offs and requests for an interview

Return JSON only, in this form: {{"claims": ["Alex built a RAG chatbot with LangChain.", ...]}}
Return {{"claims": []}} if there is nothing factual about the candidate.

Cover letter:
{actual_output}

JSON:"""


class OpenRouterJudge:
    """Adapter that lets DeepEval metrics use a LangChain chat model as their judge.

    DeepEval calls `generate(prompt, schema=SomePydanticModel)` and accepts either an instance
    of that schema or a JSON string. We ask for structured output first and fall back to plain
    text (which DeepEval parses itself) if the provider rejects the schema.

    Built as a subclass of `DeepEvalBaseLLM` in `make_judge()`, so importing this module
    doesn't import DeepEval (slow, and only installed with the dev dependencies).
    """

    def __init__(self, llm: BaseChatModel, model_name: str) -> None:
        self.llm = llm
        self.model_name = model_name

    def load_model(self) -> BaseChatModel:
        return self.llm

    def generate(self, prompt: str, schema: type[BaseModel] | None = None) -> Any:
        """Answer one judge prompt: a schema instance if `schema` is given, else plain text."""
        if schema is not None:
            try:
                answer = self.llm.with_structured_output(schema).invoke(prompt)
                if isinstance(answer, schema):
                    return answer
            except Exception as exc:  # e.g. a schema the provider can't enforce
                logger.warning("Judge structured output failed (%s); using plain JSON", exc)
        return str(self.llm.invoke(prompt).content)

    async def a_generate(self, prompt: str, schema: type[BaseModel] | None = None) -> Any:
        return await asyncio.to_thread(self.generate, prompt, schema)

    def get_model_name(self) -> str:
        return f"{self.model_name} (via OpenRouter)"


def make_judge(llm: BaseChatModel, model_name: str) -> Any:
    """A DeepEval-compatible judge built on `llm`."""
    from deepeval.models import DeepEvalBaseLLM

    class _Judge(OpenRouterJudge, DeepEvalBaseLLM):
        def __init__(self) -> None:
            OpenRouterJudge.__init__(self, llm, model_name)
            DeepEvalBaseLLM.__init__(self, model_name)

    return _Judge()


def make_metrics(judge: Any) -> dict[str, Any]:
    """The three DeepEval metrics, all judged by `judge`, run one at a time (no async).

    - faithfulness: every statement in the letter must be backed by the context (approved
      claims, profile basics and the posting). `penalize_ambiguous_claims` makes "can't tell
      from the context" count as unfaithful, which is what an invented skill looks like.
    - answer_relevancy: does the chat answer address the question that was asked?
    - tailoring (G-Eval): our rubric "tailored to the posting, no generic filler".
    """
    from deepeval.metrics import AnswerRelevancyMetric, FaithfulnessMetric, GEval
    from deepeval.test_case import SingleTurnParams

    # The default template treats every sentence as a claim, so "I'm excited to apply" was
    # judged "not in the context" and pulled honest letters down to ~0.5 (first run, see
    # docs/EVALUATION.md). We only want to check facts about the candidate.
    class CandidateFactsTemplate(_faithfulness_template_base()):
        @staticmethod
        def generate_claims(actual_output: str) -> str:
            return CANDIDATE_CLAIMS_PROMPT.format(actual_output=actual_output)

    return {
        "faithfulness": FaithfulnessMetric(
            threshold=FAITHFULNESS_THRESHOLD,
            model=judge,
            async_mode=False,
            penalize_ambiguous_claims=True,
            evaluation_template=CandidateFactsTemplate,
        ),
        "answer_relevancy": AnswerRelevancyMetric(
            threshold=RELEVANCY_THRESHOLD, model=judge, async_mode=False
        ),
        "tailoring": GEval(
            name="Tailored, no generic filler",
            evaluation_steps=TAILORING_STEPS,
            evaluation_params=[SingleTurnParams.INPUT, SingleTurnParams.ACTUAL_OUTPUT],
            threshold=TAILORING_THRESHOLD,
            model=judge,
            async_mode=False,
        ),
    }


def _faithfulness_template_base() -> type:
    """DeepEval's default faithfulness prompt set (the class we override one prompt of)."""
    import inspect

    from deepeval.metrics import FaithfulnessMetric

    return inspect.signature(FaithfulnessMetric.__init__).parameters["evaluation_template"].default
