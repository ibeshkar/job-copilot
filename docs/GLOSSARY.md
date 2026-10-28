# Glossary

One line per term, in alphabetical order. Added to after every phase.

- **`.env` file**: a local text file with secret settings (like API keys) that is never committed to git.
- **AppTest**: Streamlit's tool for running an app in a test without a browser.
- **`.env.example`**: a committed copy of `.env` with empty values, so others know which settings exist without seeing your secrets.
- **Engine (SQLAlchemy)**: the object that knows how to connect to the database; created once and reused.
- **Entry point**: the file you start the app with; here `app/main.py`.
- **Fixture (pytest)**: setup code that gives a test something it needs, such as an empty database.
- **In-memory database**: a SQLite database that lives only in RAM and disappears after the test; it keeps tests fast and isolated.
- **Lint / linter (ruff)**: a tool that checks code for mistakes and style problems without running it.
- **OpenRouter**: a service that forwards requests in the OpenAI API format to many model providers (OpenAI, Anthropic, Google, ...) with one key.
- **ORM (Object-Relational Mapper)**: a library that lets you work with database rows as Python objects instead of writing SQL by hand. We use SQLModel.
- **pydantic-settings**: a library that reads settings from environment variables and `.env` and checks their types.
- **Repository (repo) layer**: the module with all database functions (`app/db/repo.py`); other code calls it instead of writing queries.
- **`SecretStr`**: a Pydantic type that hides a secret's value when printed or logged.
- **Session (database)**: one unit of work with the database; changes are saved together on `commit`.
- **SQL injection**: an attack where user input is run as SQL; the ORM prevents it by sending values separately from the query.
- **SQLModel**: an ORM that combines Pydantic models and SQLAlchemy tables in one class.
- **`st.navigation`**: Streamlit's way to define a multipage app and its sidebar menu in code.
- **Streamlit rerun**: Streamlit runs the whole page script again on every click; anything that should happen once must be cached.
- **`st.cache_resource`**: a Streamlit decorator that runs a function once per server process and reuses the result.
- **thread_id**: the name of one saved conversation or workflow run in LangGraph; each job gets `job-<id>`.
- **uv**: a fast Python package and project manager; `uv sync` installs everything from `pyproject.toml`.
