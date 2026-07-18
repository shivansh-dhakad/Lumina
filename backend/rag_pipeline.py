import logging
import os
import os.path
import re
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
STRICT OUTPUT FORMAT — follow this exactly, with no deviation:
- Plain text only. NO markdown headings (#, ##, ###). NO code fences (```). NO bullet
  characters (-, *, +). NO nested lists. NO bold/italic markers (**, _).
- Every question has exactly 4 options, labelled "A." "B." "C." "D." on their own lines.
- Every question ends with one "Answer: <letter>" line and one "Explanation: <text>" line.
- Do not add any text before question 1 or after the final explanation.

Here are two example questions in the required format (do not reuse this content):

1. What does a binary semaphore enforce?
A. Deadlock between processes
B. Mutual exclusion over a shared resource
C. Faster memory access
D. Process priority
Answer: B
Explanation: A binary semaphore lets only one process access the critical section at a time, preventing race conditions.

2. What is the purpose of a rotating daisy chain?
A. To increase memory bandwidth
B. To give the highest bus priority to the device nearest the controller
C. To rotate priority to the device following the one that last held the bus
D. To disable interrupts during I/O
Answer: C
Explanation: In a rotating daisy chain, the last device serviced passes priority to its neighbor, so priority rotates fairly instead of always favoring the same device.

Now generate exactly {num_questions} NEW questions in this same format, numbered 1. through {num_questions}., one blank line between questions, based only on the context below.

Context:
{context}

Return ONLY the {num_questions} questions in the exact format shown. No preamble, no closing remarks, no extra commentary.
""")

FLASHCARDS_PROMPT = ChatPromptTemplate.from_template("""
You are an educational flashcard creator. Create flashcards from the context below.
{topic_note}
Instructions:
- Generate exactly {num_cards} flashcards covering the most important concepts, definitions, and facts.
- Do NOT use headings (#, ##), bullet lists, or any Markdown structure other than shown below.
- Format EVERY flashcard EXACTLY like this example, with nothing else on those lines:

**Q:** What is a binary semaphore?
**A:** A low-level primitive that enforces mutual exclusion so only one process accesses a shared resource at a time.
**Explain:** It works by having a process wait on the semaphore before entering the critical section and signal it when done.
---

- Repeat that exact 4-line block {num_cards} times total, separated by a line containing only "---".
- Keep the answer and the explanation to 1-2 short sentences each.
- Use simple, clear language suitable for a student.

Context:
{context}

Return ONLY the {num_cards} flashcards in the format above. No preamble, no closing remarks, no extra commentary.
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
- List exactly {num_terms} important terms, concepts, or names from the material.
- Output ONLY a Markdown table — no heading, no intro sentence, no text before or after it.
- The table must have exactly two columns: Term | Definition
- Definitions must be one clear, short sentence each, based only on the context.
- Sort terms alphabetically.

Example of the exact format expected (do not reuse this content):
| Term | Definition |
| --- | --- |
| Binary Semaphore | A primitive that enforces mutual exclusion over a shared resource. |

Context:
{context}

Return ONLY the Markdown table (header row + separator row + {num_terms} data rows). No preamble, no closing remarks.
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


# ── Server-side format validation (mirrors the parsers in web/app.js) ──────
# The local LLM occasionally ignores formatting instructions. Rather than let
# a malformed response reach the user as raw markdown, we validate it here
# and retry generation before giving up.

# The local LLM occasionally ignores formatting instructions, or stops short
# of the requested count. Rather than let a malformed/partial response reach
# the user, we extract whichever individual items ARE well-formed from each
# attempt, keep them, and only ask the model to fill in whatever's still
# missing — instead of accepting a short batch as "good enough".

_QUIZ_BLOCK_RE = re.compile(r"\n(?=\s*\d+[.)]\s)")
_QUIZ_QUESTION_RE = re.compile(r"^\s*\d+[.)]\s*(.+?)(?:\n|$)", re.S)
_QUIZ_ANSWER_RE = re.compile(r"Answer:\s*([A-D])", re.I)


def _clean_llm_text(text: str) -> str:
    return re.sub(r"^#{1,6}\s.*$", "", text, flags=re.M).replace("```", "")


def _extract_quiz_blocks(text: str) -> list[str]:
    """Return only the individual question blocks that are well-formed."""
    cleaned = _clean_llm_text(text)
    blocks = _QUIZ_BLOCK_RE.split(cleaned)
    valid = []
    for block in blocks:
        q_match = _QUIZ_QUESTION_RE.match(block)
        if not q_match or not q_match.group(1).strip() or len(q_match.group(1)) > 300:
            continue
        options = sum(
            1 for letter in "ABCD"
            if re.search(rf"^\s*\**{letter}[.)\]:]\**\s*\S", block, re.M | re.I)
        )
        if options >= 2 and _QUIZ_ANSWER_RE.search(block):
            valid.append(block.strip())
    return valid


def _extract_flashcard_blocks(text: str) -> list[str]:
    cleaned = _clean_llm_text(text)
    starts = [m.start() for m in re.finditer(r"\*\*Q:\*\*", cleaned)]
    blocks = []
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(cleaned)
        chunk = cleaned[start:end]
        q_match = re.search(r"\*\*Q:\*\*\s*(.+?)(?:\n|$)", chunk)
        a_match = re.search(
            r"\*\*A:\*\*\s*(.+?)(?=\n\s*\*\*Explain:\*\*|\n\s*-{3,}|$)", chunk, re.S
        )
        if q_match and a_match and len(q_match.group(1)) <= 300 and len(a_match.group(1)) <= 500:
            blocks.append(chunk.strip().rstrip("-").strip())
    return blocks


def _extract_glossary_rows(text: str) -> list[str]:
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("|") or re.match(r"^\|?\s*:?-{2,}", line):
            continue
        cells = [c.strip() for c in line.split("|") if c.strip()]
        if len(cells) < 2:
            continue
        term, definition = cells[0], " — ".join(cells[1:])
        if term.lower() == "term" or definition.lower() == "definition":
            continue
        if len(term) > 120 or len(definition) > 400:
            continue
        rows.append(f"| {term} | {definition} |")
    return rows


_EXTRACTORS = {
    "quiz": _extract_quiz_blocks,
    "flashcards": _extract_flashcard_blocks,
    "glossary": _extract_glossary_rows,
}


def _dedup_key(tool: str, block: str) -> str:
    if tool == "quiz":
        m = _QUIZ_QUESTION_RE.match(block)
        return m.group(1).strip().lower()[:60] if m else block[:60].lower()
    if tool == "flashcards":
        m = re.search(r"\*\*Q:\*\*\s*(.+?)(?:\n|$)", block)
        return m.group(1).strip().lower()[:60] if m else block[:60].lower()
    if tool == "glossary":
        cells = [c.strip() for c in block.split("|") if c.strip()]
        return cells[0].lower() if cells else block[:60].lower()
    return block[:60].lower()


def _assemble(tool: str, blocks: list[str]) -> str:
    if tool == "quiz":
        renumbered = []
        for i, block in enumerate(blocks, 1):
            body = re.sub(r"^\s*\d+[.)]\s*", "", block, count=1)
            renumbered.append(f"{i}. {body}")
        return "\n\n".join(renumbered)
    if tool == "flashcards":
        return "\n---\n".join(blocks) + "\n---"
    if tool == "glossary":
        return "| Term | Definition |\n| --- | --- |\n" + "\n".join(blocks)
    return "\n\n".join(blocks)


_RETRY_NOTE = (
    "\n\nIMPORTANT: Your previous attempt did not follow the required format "
    "closely enough. Follow the format instructions and example EXACTLY this "
    "time — plain text only, no headings, no bullet symbols, no code fences.\n"
)

MAX_GENERATION_ATTEMPTS = 4


def _invoke_with_backfill(chain, inputs: dict, tool: str, expected_count: int):
    """Invoke the generation chain, keeping only well-formed items across
    attempts and asking for just the shortfall each time, until the full
    requested count is reached (or attempts run out)."""
    extractor = _EXTRACTORS.get(tool)
    if extractor is None:
        return chain.invoke(inputs, config={"callbacks": []})  # e.g. summary/compare

    collected: list[str] = []
    seen_keys: set[str] = set()

    for attempt in range(1, MAX_GENERATION_ATTEMPTS + 1):
        remaining = expected_count - len(collected)
        call_inputs = {
            **inputs,
            "num_questions": remaining,
            "num_cards": remaining,
            "num_terms": remaining,
        }
        text = chain.invoke(call_inputs, config={"callbacks": []})

        for block in extractor(text):
            if len(collected) >= expected_count:
                break
            key = _dedup_key(tool, block)
            if key in seen_keys:
                continue  # model repeated an earlier item — skip it
            seen_keys.add(key)
            collected.append(block)

        if len(collected) >= expected_count:
            break

        logger.warning(
            "generate_tool(%s) attempt %d/%d: have %d/%d well-formed items, "
            "asking for %d more",
            tool, attempt, MAX_GENERATION_ATTEMPTS, len(collected),
            expected_count, expected_count - len(collected),
        )
        note = _RETRY_NOTE
        if collected:
            already = "; ".join(_dedup_key(tool, b) for b in collected)
            note += f"Do not repeat any of these already-used items: {already}\n"
        inputs = {**inputs, "topic_note": inputs.get("topic_note", "") + note}

    if not collected:
        raise ValueError(
            f"The model couldn't produce a well-formatted {tool} after "
            f"{MAX_GENERATION_ATTEMPTS} attempts. Try again, or use a smaller "
            f"item count."
        )
    if len(collected) < expected_count:
        logger.warning(
            "generate_tool(%s) returning %d/%d items after %d attempts — "
            "model couldn't reach the full count",
            tool, len(collected), expected_count, MAX_GENERATION_ATTEMPTS,
        )
    return _assemble(tool, collected)


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


# Default item counts, and the allowed range a user can request via the UI.
TOOL_ITEM_DEFAULTS = {"quiz": 5, "flashcards": 9, "glossary": 12}
TOOL_ITEM_MIN, TOOL_ITEM_MAX = 3, 25


def _resolve_count(tool: str, count) -> int:
    default = TOOL_ITEM_DEFAULTS.get(tool)
    if default is None:
        return 0
    try:
        n = int(count) if count is not None else default
    except (TypeError, ValueError):
        n = default
    return max(TOOL_ITEM_MIN, min(n, TOOL_ITEM_MAX))


def generate_tool(tool: str, filepath, topic: str | None = None, count: int | None = None) -> str:
    if tool not in TOOL_PROMPTS:
        raise ValueError(f"Unknown tool '{tool}'. Must be one of: {list(TOOL_PROMPTS)}")
    filepaths = _as_filepath_list(filepath)
    if tool == "compare" and len(filepaths) < 2:
        raise ValueError("Compare requires at least 2 sources in this chat.")
    topic = (topic or "").strip() or None
    n = _resolve_count(tool, count)
    try:
        for fp in filepaths:
            index_document(fp)

        embedding_model = get_embedding_model(settings.embedding_model)
        store = get_or_create_store(settings.chroma_persist_dir, embedding_model)
        chat_model = get_chat_model(settings.llm_model)
        query_text = topic if topic else "key concepts, definitions, and main topics"
        # Pull more context chunks when a larger item count is requested so the
        # model has enough distinct material to draw from.
        retrieval_k = max(5, min(n, 12)) if n else 5
        docs = _retrieve(store, query_text, filepaths, k=retrieval_k, lambda_mult=0.7)

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
        inputs = {
            "context": context,
            "topic_note": topic_note,
            "num_questions": n,
            "num_cards": n,
            "num_terms": n,
        }
        return _invoke_with_backfill(chain, inputs, tool, expected_count=n)
    except Exception:
        logger.exception(
            "generate_tool failed (tool=%r, filepaths=%r, topic=%r, count=%r)",
            tool, filepaths, topic, count,
        )
        raise