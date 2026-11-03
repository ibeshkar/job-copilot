# What: the password-protected Developer settings page: model, temperature and max tokens;
#       token usage and cost by action; LangSmith status; a read-only prompt viewer; and the
#       raw saved graph state of a job.
# Why: developer knobs are kept apart from the user's view (feature S6), and the password
#      (APP_DEV_PASSWORD) stops a casual user from changing the model or reading internals.
# Used by: app/main.py (st.navigation). Work is done by app/services/dev_settings.py,
#          app/services/usage.py and app/services/observability.py.

import streamlit as st

from app import config as C
from app.config import CHAT_MODELS, get_settings
from app.services import applications, dev_settings, usage
from app.services.observability import tracing_status
from app.ui.components import (
    busy_button,
    clear_form_errors,
    error_summary,
    fail_form,
    field_error,
    form_errors,
)
from app.ui.state import session_started_at
from app.utils.errors import InvalidInputError
from app.validation.models import PasswordInput, SettingsInput, check_input

UNLOCK_KEY = "dev_unlocked"

st.title("🛠️ Developer settings")

if not st.session_state.get(UNLOCK_KEY):
    st.caption("For the developer only. The password is `APP_DEV_PASSWORD` in `.env`.")
    login_errors = form_errors("dev_login")
    with st.form("dev_login"):
        attempt = st.text_input("Password *", type="password", max_chars=C.PASSWORD_MAX_CHARS)
        field_error(login_errors, "password")
        if st.form_submit_button("Unlock", type="primary"):
            clean, errors = check_input(PasswordInput, {"password": attempt})
            if clean is None:
                fail_form("dev_login", errors)
            if dev_settings.check_password(attempt):
                clear_form_errors("dev_login")
                st.session_state[UNLOCK_KEY] = True
                st.rerun()
            fail_form("dev_login", {"password": "Wrong password."})
    st.stop()

if dev_settings.uses_default_password():
    st.warning(
        "`APP_DEV_PASSWORD` is still the default `change-me`. Set your own in `.env`.", icon="🔓"
    )
if st.button("🔒 Lock"):
    st.session_state[UNLOCK_KEY] = False
    st.rerun()

model_tab, usage_tab, tracing_tab, prompts_tab, state_tab = st.tabs(
    ["🤖 Model", "💰 Usage & cost", "📡 LangSmith", "📝 Prompts", "🔍 Graph state"]
)

with model_tab:
    settings = get_settings()
    st.caption(
        "Applies to every new AI step, for all pages, until you reset it. Saved in "
        "`data/runtime/dev_settings.json`; it wins over `.env`."
    )
    errors = form_errors("llm_settings")
    with st.form("llm_settings"):
        error_summary(errors, SettingsInput)
        model = st.selectbox(
            "Model (OpenRouter id)",
            CHAT_MODELS,  # only these can be saved (SettingsInput); they have prices
            index=CHAT_MODELS.index(settings.default_model)
            if settings.default_model in CHAT_MODELS
            else 0,
            help="Add more models to MODEL_PRICING and MODEL_MAX_OUTPUT_TOKENS in app/config.py.",
        )
        field_error(errors, "default_model")
        temperature = st.slider(
            "Temperature",
            min_value=C.TEMPERATURE_MIN,
            max_value=C.TEMPERATURE_MAX,
            value=min(max(float(settings.temperature), C.TEMPERATURE_MIN), C.TEMPERATURE_MAX),
            step=0.05,
            help="0 = predictable, higher = more varied. Low is better for extraction.",
        )
        field_error(errors, "temperature")
        top = max(C.MODEL_MAX_OUTPUT_TOKENS.values())
        max_tokens = st.number_input(
            "Max tokens per answer",
            min_value=C.MAX_TOKENS_MIN,
            max_value=top,
            value=min(max(int(settings.max_tokens), C.MAX_TOKENS_MIN), top),
            step=64,
            help="Too low cuts structured answers off. The limit depends on the model: "
            + ", ".join(f"{m} {n:,}" for m, n in C.MODEL_MAX_OUTPUT_TOKENS.items()),
        )
        field_error(errors, "max_tokens")
        if busy_button("Save", key="llm_settings_save", form=True, type="primary"):
            try:
                dev_settings.save_llm_overrides(model, temperature, int(max_tokens))
            except InvalidInputError as exc:
                fail_form("llm_settings", exc.errors)
            clear_form_errors("llm_settings")
            st.rerun()  # the sidebar now shows the new model
    if dev_settings.has_overrides() and st.button("↩️ Reset to .env values"):
        dev_settings.reset_llm_overrides()
        st.rerun()

with usage_tab:
    session_usage = usage.usage_since(session_started_at())
    all_time = usage.usage_since(None)
    col_session, col_total = st.columns(2)
    col_session.metric("This session", session_usage.label())
    col_total.metric("All time", all_time.label())
    budget = get_settings().session_token_budget
    st.caption(f"Session budget: {budget:,} tokens (warning at 80%). Embeddings aren't counted.")
    rows = usage.usage_report()
    if rows:
        st.dataframe(rows, hide_index=True, use_container_width=True)
    else:
        st.info("No AI actions yet.", icon="💰")
    st.caption(
        "Prices come from `MODEL_PRICING` in `app/config.py` ($ per 1M tokens). "
        "Check https://openrouter.ai/models; they change."
    )

with tracing_tab:
    st.markdown(f"**Status:** {tracing_status()}")
    version, _ = dev_settings.prompts()
    st.markdown(
        "Every run is tagged with its **action** (e.g. `analyse_fit`, `draft`, `chat`) and has "
        f"the metadata `prompt_version={version}`, `action` and `job_id` / `thread_id`."
    )
    st.caption(
        "Turn on with LANGSMITH_TRACING=true and LANGSMITH_API_KEY in .env, then restart. "
        "LANGSMITH_HIDE_INPUTS=true keeps texts (CVs, postings) out of the traces."
    )

with prompts_tab:
    version, all_prompts = dev_settings.prompts()
    st.caption(f"Read-only. Edit them in `app/agent/prompts.py`. Version `{version}`.")
    name = st.selectbox("Prompt", list(all_prompts))
    st.code(all_prompts[name], language="markdown", wrap_lines=True)

with state_tab:
    choices = applications.list_job_choices()
    if not choices:
        st.info("No jobs yet.", icon="🔍")
    else:
        labels = {job_id: label for job_id, label in choices}
        job_id = st.selectbox("Job", list(labels), format_func=lambda i: f"#{i} {labels[i]}")
        st.json(dev_settings.raw_graph_state(job_id), expanded=1)
