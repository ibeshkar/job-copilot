# DocuMind: chat with your company's policy documents

DocuMind is a **RAG chatbot** I built during the Turing College AI Engineering programme. Employees
ask questions in plain language ("How many days can I work remotely?") and get an answer with
citations to the exact policy document and section.

## Why I built it
At my logistics job, people asked the same HR and safety questions every week. The answers were
in 30+ PDF documents nobody wanted to search. DocuMind answers in seconds and shows the source,
so people can trust the answer.

## What it does
- Upload PDF, DOCX and Markdown documents through a Streamlit UI.
- Splits documents into chunks of about 500 characters and embeds them with OpenAI
  `text-embedding-3-small`.
- Stores the vectors in a persistent **ChromaDB** collection with metadata (file, page, section).
- Answers questions with **LangChain** and `gpt-4o-mini`, using the top 4 chunks as context.
- Shows citations under every answer.
- Says "I don't know" when no chunk is relevant enough, instead of guessing.

## Tech stack
Python 3.12, LangChain, ChromaDB, OpenAI API, Streamlit, Pydantic, pytest, Docker.

## Evaluation
- Built a test set of 40 questions with expected source documents.
- Retrieval hit rate (the right document in the top 4): **88%**.
- Measured answer faithfulness with DeepEval: average **0.91**.
- Added a query-rewriting step for short questions, which raised the hit rate from 81% to 88%.

## What I learned
- Chunk size matters more than the model for retrieval quality.
- Users trust answers much more when they can click the source.
- Evaluating with a fixed test set is the only way to know whether a change helped.

## Run it
```
docker compose up
```
