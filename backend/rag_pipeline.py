import logging
import os
import os.path
import threading
from pathlib import Path

import chromadb
from langchain_chroma import Chroma
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

from backend.config import settings
from backend.document_loader import load_and_split
from backend.models import get_chat_model, get_embedding_model

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_chroma_client = None
_store = None

MODE_INSTRUCTIONS = {
    "normal": "",
    "simplify": (
        "\nUse simpler language and shorter sentences. Replace jargon with plain words.\n"
    ),
    "eli5": (
        "\nExplain like the student is 12 years old. Use analogies and everyday examples.\n"
    ),
    "exam": (
        "\nFocus on exam-relevant facts, definitions, and likely test questions. "
        "Be concise, structured, and highlight what to memorise.\n"
    ),
}

PROMPT = ChatPromptTemplate.from_template("""
You are an intelligent educational AI assistant.
Answer the question using ONLY the information in the context below.
{history_block}{mode_instruction}
Write a complete answer of at least 4-6 sentences (more if the topic needs it).
Never reply with just a single word, a title, or a bare term — always explain it.
If the student refers to something from the previous conversation, use that context
together with the retrieved passages below.

Context:
{context}

Question:
{question}

Write the full explanation now, in simple student-friendly language, as valid Markdown:
- Start with a level-2 heading (`##`) naming the concept.
- Explain it in your own words, in a short paragraph.
- Add a bullet list of key points, and a small example if the context supports one.
- Use **bold** for important terms; use a table only if it genuinely helps a comparison.
- Do NOT invent information outside the context.
- If the context truly does not contain the answer, reply exactly:
  "I don't know based on the provided documents."
""")

QUIZ_PROMPT = ChatPromptTemplate.from_template("""
You are an educational quiz generator. Create a quiz from the context below.
{topic_note}
Instructions:
- Generate exactly 5 multiple-choice questions (A, B, C, D).
- Each question must test understanding of a key concept.
- Number each question (1., 2., …).
- After each set of 4 options, write "Answer: [letter]" on its own line.
- Format the entire output as valid Markdown.

Context:
{context}

Return ONLY the quiz in Markdown.
""")

FLASHCARDS_PROMPT = ChatPromptTemplate.from_template("""
You are an educational flashcard creator. Create flashcards from the context below.
{topic_note}
Instructions:
- Generate 8–10 flashcards covering the most important concepts, definitions, and facts.
- Format each flashcard exactly as:
  **Q:** [question]
  **A:** [answer]
- Use simple, clear language suitable for a student.

Context:
{context}

Return ONLY the flashcards in Markdown.
""")

SUMMARY_PROMPT = ChatPromptTemplate.from_template("""
You are an educational summarizer. Write a comprehensive summary of the context below.
{topic_note}
Instructions:
- Use a level-2 heading (`##`) for the overall title.
- Organise content into sections with level-3 headings (`###`).
- Use bullet points for key points within each section.
- Highlight important terms in **bold**.
- Write in clear, student-friendly language.
- End with a "## Key Takeaways" section listing the 3–5 most important points.

Context:
{context}

Return ONLY the summary in Markdown.
""")

GLOSSARY_PROMPT = ChatPromptTemplate.from_template("""
You are an educational glossary builder. Extract key terms from the context below.
{topic_note}
Instructions:
- List 10–15 important terms, concepts, or names from the material.
- Format as a Markdown table with columns: **Term** | **Definition**
- Definitions must be one clear sentence each, based only on the context.
- Sort terms alphabetically.

Context:
{context}

Return ONLY the glossary table in Markdown.
""")

COMPARE_PROMPT = ChatPromptTemplate.from_template("""
You are an educational analyst comparing multiple sources provided in the context below.
{topic_note}
Instructions:
- Use a level-2 heading (`##`) for the comparison title.
- Identify what each source covers (sources are labelled in the context).
- Create a comparison table: **Aspect** | **Source 1** | **Source 2** (add columns as needed).
- Add a "### Overlap" section listing shared themes.
- Add a "### Differences" section listing unique points per source.
- End with "### When to use which" — brief guidance for a student.

Context:
{context}

Return ONLY the comparison in Markdown.
""")

SUGGEST_PROMPT = ChatPromptTemplate.from_template("""
Based on the study material below, write exactly 4 short questions a student would ask
to understand this content. Each question must be under 80 characters.
Return ONLY the 4 questions, one per line, with no numbering or bullets.

Context:
{context}
""")

TOOL_PROMPTS = {
    "quiz": QUIZ_PROMPT,
    "flashcards": FLASHCARDS_PROMPT,
    "summary": SUMMARY_PROMPT,
    "glossary": GLOSSARY_PROMPT,
    "compare": COMPARE_PROMPT,
}


def get_or_create_store(persist_dir: str, embedding_model) -> Chroma:
    global _chroma_client, _store
    with _lock:
        if _store is None:
            Path(persist_dir).mkdir(parents=True, exist_ok=True)
            _chroma_client = chromadb.PersistentClient(path=persist_dir)
            _store = Chroma(
                client=_chroma_client,
                collection_name="lumina_sources",
                embedding_function=embedding_model,
            )
            logger.info("Chroma store initialised at %s", persist_dir)
        return _store


def add_documents(store: Chroma, documents, source_id: str) -> bool:
    if not documents:
        return False

    existing = store.get(where={"source": source_id}, include=[])
    if existing.get("ids"):
        logger.info("Source already indexed: %s", source_id)
        return False

    store.add_documents(documents)
    logger.info("Indexed %d chunks from %s", len(documents), source_id)
    return True


def index_document(filepath: str) -> None:
    try:
        chunks = load_and_split(
            filepath,
            chunk_size=settings.chunk_size,
            chunk_overlap=settings.chunk_overlap,
        )
        embedding_model = get_embedding_model(settings.embedding_model)
        store = get_or_create_store(settings.chroma_persist_dir, embedding_model)
        source_id = os.path.basename(filepath)
        add_documents(store, chunks, source_id=source_id)
    except Exception:
        logger.exception("Failed to index document: %s", filepath)
        raise


def _as_filepath_list(filepaths) -> list[str]:
    if isinstance(filepaths, str):
        return [filepaths]
    filepaths = list(filepaths)
    if not filepaths:
        raise ValueError("At least one source is required.")
    return filepaths


def _source_filter(filepaths: list[str]) -> dict:
    source_ids = [os.path.basename(fp) for fp in filepaths]
    if len(source_ids) == 1:
        return {"source": source_ids[0]}
    return {"source": {"$in": source_ids}}


def _retrieve(store, query_text: str, filepaths: list[str], k: int, lambda_mult: float):
    fetch_k = max(k * 4, 20)
    where = _source_filter(filepaths)
    try:
        return store.max_marginal_relevance_search(
            query_text, k=k, fetch_k=fetch_k, lambda_mult=lambda_mult, filter=where,
        )
    except Exception:
        logger.warning("MMR search failed, falling back to similarity search")
        return store.similarity_search(query_text, k=k, filter=where)


def _format_history(history: list[dict] | None) -> str:
    if not history:
        return ""
    lines = []
    for turn in history[-6:]:
        role = str(turn.get("role", "")).strip()
        content = str(turn.get("content", "")).strip()
        if role in {"user", "assistant"} and content:
            label = "Student" if role == "user" else "Assistant"
            lines.append(f"{label}: {content}")
    if not lines:
        return ""
    return "\n\nPrevious conversation (for follow-up context):\n" + "\n".join(lines) + "\n"


def _build_context(docs) -> tuple[str, list[dict]]:
    citations: list[dict] = []
    seen: set[tuple[str, str, int]] = set()
    parts: list[str] = []

    for index, doc in enumerate(docs, 1):
        source = doc.metadata.get("source", "unknown")
        page = doc.metadata.get("page")
        page_label = doc.metadata.get("page_label")

        excerpt = doc.page_content.strip()
        if len(excerpt) > 220:
            excerpt = excerpt[:220] + "…"

        key = (source, excerpt[:80], page)
        if key not in seen:
            seen.add(key)
            cite_dict = {
                "index": len(citations) + 1,
                "source": source,
                "excerpt": excerpt,
            }
            if page is not None:
                cite_dict["page"] = page
            if page_label is not None:
                cite_dict["page_label"] = page_label
            citations.append(cite_dict)

        page_info = f"page {page + 1}" if page is not None else ""
        if page_label:
            page_info = f"page {page_label}"

        source_label = f"source: {source}"
        if page_info:
            source_label += f", {page_info}"

        parts.append(f"[{index}] ({source_label})\n{doc.page_content}")

    return "\n\n".join(parts), citations


def query(
    question: str,
    filepath,
    history: list[dict] | None = None,
    mode: str = "normal",
) -> dict:
    """Answer a question and return the answer plus source citations."""
    filepaths = _as_filepath_list(filepath)
    mode = (mode or "normal").strip().lower()
    if mode not in MODE_INSTRUCTIONS:
        mode = "normal"
    try:
        for fp in filepaths:
            index_document(fp)

        embedding_model = get_embedding_model(settings.embedding_model)
        store = get_or_create_store(settings.chroma_persist_dir, embedding_model)
        chat_model = get_chat_model(settings.llm_model)

        docs = _retrieve(
            store, question, filepaths,
            k=settings.retriever_k, lambda_mult=settings.retriever_lambda_mult,
        )

        context, citations = _build_context(docs)
        history_block = _format_history(history)
        mode_instruction = MODE_INSTRUCTIONS[mode]
        chain = PROMPT | chat_model | StrOutputParser()
        answer = chain.invoke(
            {
                "context": context,
                "question": question,
                "history_block": history_block,
                "mode_instruction": mode_instruction,
            },
            config={"callbacks": []},
        )
        return {"answer": answer, "citations": citations}
    except Exception:
        logger.exception("Query failed (question=%r, filepaths=%r)", question, filepaths)
        raise


def suggest_questions(filepath) -> list[str]:
    """Return starter questions based on the indexed source content."""
    filepaths = _as_filepath_list(filepath)
    for fp in filepaths:
        index_document(fp)

    embedding_model = get_embedding_model(settings.embedding_model)
    store = get_or_create_store(settings.chroma_persist_dir, embedding_model)
    chat_model = get_chat_model(settings.llm_model)

    docs = _retrieve(
        store, "main topics overview key concepts", filepaths,
        k=4, lambda_mult=0.8,
    )
    if not docs:
        return [
            "What are the main topics in this source?",
            "Summarise the key concepts.",
            "What should I focus on for an exam?",
            "Explain the most important idea simply.",
        ]

    context, _ = _build_context(docs)
    chain = SUGGEST_PROMPT | chat_model | StrOutputParser()
    raw = chain.invoke({"context": context}, config={"callbacks": []})
    questions = [
        line.strip().lstrip("0123456789.-) ")
        for line in raw.strip().splitlines()
        if line.strip()
    ]
    return questions[:4] if questions else [
        "What are the main topics in this source?",
        "Summarise the key concepts.",
        "What should I focus on for an exam?",
        "Explain the most important idea simply.",
    ]


def generate_tool(tool: str, filepath, topic: str | None = None) -> str:
    if tool not in TOOL_PROMPTS:
        raise ValueError(f"Unknown tool '{tool}'. Must be one of: {list(TOOL_PROMPTS)}")
    filepaths = _as_filepath_list(filepath)
    if tool == "compare" and len(filepaths) < 2:
        raise ValueError("Compare requires at least 2 sources in this chat.")
    topic = (topic or "").strip() or None
    try:
        for fp in filepaths:
            index_document(fp)

        embedding_model = get_embedding_model(settings.embedding_model)
        store = get_or_create_store(settings.chroma_persist_dir, embedding_model)
        chat_model = get_chat_model(settings.llm_model)

        query_text = topic if topic else "key concepts, definitions, and main topics"
        docs = _retrieve(store, query_text, filepaths, k=5, lambda_mult=0.7)

        if not docs:
            if topic:
                raise ValueError(
                    f"No content matching the topic '{topic}' was found in the selected source(s)."
                )
            raise ValueError("No indexed content was found in the selected source(s).")

        context, _ = _build_context(docs)
        topic_note = (
            f"\nFocus specifically on the topic: \"{topic}\". Ignore parts of the "
            f"context that are unrelated to it.\n"
            if topic else ""
        )
        prompt = TOOL_PROMPTS[tool]
        chain = prompt | chat_model | StrOutputParser()
        return chain.invoke(
            {"context": context, "topic_note": topic_note},
            config={"callbacks": []},
        )
    except Exception:
        logger.exception(
            "generate_tool failed (tool=%r, filepaths=%r, topic=%r)", tool, filepaths, topic
        )
        raise
