# Concepts

The course concepts, each explained in three layers: one sentence, an analogy, and where it is
in our code. Sections are added as the phases build them.

---

## RAG (Retrieval-Augmented Generation)

**One sentence:** before the AI answers, we search the user's own documents and give it the most
relevant pieces, so it answers from facts instead of from memory or imagination.

**Analogy:** an open-book exam. The student (the LLM) is smart but forgetful. RAG is the
index at the back of the textbook that points to the right page before they write the answer.

**The steps:**
1. **Load**: get plain text out of PDF, DOCX, MD and TXT files.
2. **Chunk**: cut the text into pieces of about 500 characters (roughly one paragraph).
3. **Embed**: turn each chunk into a list of 1,536 numbers (a vector) that captures its meaning.
4. **Store**: save the vectors in Chroma, a vector database on disk.
5. **Retrieve**: embed the question the same way and return the chunks whose vectors are closest.

**Where it is in our code:** `app/rag/ingest.py` → `ingest_file()` and
`app/rag/retriever.py` → `search_evidence()`.

```python
def ingest_file(filename, data, doc_type=None, vectorstore=None) -> int:
    vectorstore = vectorstore or get_vectorstore()
    text = load_text(filename, data)                                  # 1. load
    chunks = split_into_chunks(text, filename, doc_type or guess_doc_type(filename))  # 2. chunk
    delete_document_chunks(filename, vectorstore)                     # re-upload = replace
    vectorstore.add_documents(chunks, ids=[c.metadata["chunk_id"] for c in chunks])  # 3+4
    return len(chunks)
```

**Why chunks and not whole files?** A CV is one long text about many skills. If we stored it as
one vector, a search for "RAG chatbot" would match the whole CV a bit. With chunks, it matches
the exact paragraph about the RAG project, and that paragraph becomes the evidence we show the
user.

**Why the overlap (80 characters)?** If a sentence crosses the border between two chunks, the
overlap makes sure both chunks contain it in full.

**Agentic RAG** (Phase 3) adds a loop on top: the agent grades the retrieved chunks and, if none
are good enough, rewrites the search query and tries again.

---

## Embeddings and vector search

**One sentence:** an embedding is a list of numbers that represents the meaning of a text, so
texts with similar meanings get similar numbers.

**Analogy:** coordinates on a map of meaning. "Built a chatbot with LangChain" and "developed
an LLM assistant" are close neighbours; "drove a forklift" is in another city.

**How "close" is measured:** cosine similarity, which compares the angle between two vectors.
We turn it into a **relevance score from 0 to 1** and store it in each `Evidence` object.

**Where it is in our code:** `app/agent/llm.py` → `get_embeddings()` (OpenRouter's
`openai/text-embedding-3-small`) and `app/rag/ingest.py` → `make_vectorstore()`, which sets
`{"hnsw": {"space": "cosine"}}`.

---

## Short-term vs. long-term memory

**One sentence:** short-term memory (the **checkpointer**) remembers where one conversation or
workflow is; long-term memory (the **Store**) remembers facts about the user across all of them.

**Analogy:** the checkpointer is the save game in a video game: one per playthrough. The Store
is your player profile: name, settings and achievements carry over to every new game.

| | Checkpointer (short-term) | Store (long-term) |
|---|---|---|
| Remembers | the state of one graph run (`thread_id`) | the user: profile, preferences, facts, style rules |
| Lifetime | one job's workflow or one chat | forever, until the user deletes it |
| Example | "this job is waiting at the claim check" | "Alex speaks German at A2 level" |
| In our code | Phase 3 (`SqliteSaver`) | `app/memory/store.py` (`SqliteStore`) |

**Namespaces:** the Store organises data like folders. A namespace is a tuple such as
`("local", "profile")`: the user id first, then the kind of memory. Inside a namespace, each
item has a key (`"main"` for the single profile, a random id for each fact).

**Where it is in our code:** `app/memory/store.py`.

```python
def _ns(kind: str, user_id: str) -> tuple[str, ...]:
    return (user_id, kind)                      # e.g. ("local", "profile")

def save_profile(profile, store=None, user_id=DEFAULT_USER_ID) -> None:
    (store or get_store()).put(_ns("profile", user_id), "main", profile.model_dump(mode="json"))

def get_profile(store=None, user_id=DEFAULT_USER_ID) -> Profile | None:
    item = (store or get_store()).get(_ns("profile", user_id), "main")
    return Profile.model_validate(item.value) if item else None
```
