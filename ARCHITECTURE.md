# Project Architecture & Technical Reference

> **LUMINA** is a fully **local**, **private** AI-powered learning assistant.
> It lets you upload documents or paste website URLs and then chat with an AI that answers questions **grounded exclusively** in your source material — no data ever leaves your machine.

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Key Features](#2-key-features)
3. [High-Level Architecture](#3-high-level-architecture)
4. [Directory Structure](#4-directory-structure)
5. [Backend Deep Dive](#5-backend-deep-dive)
6. [Frontend Deep Dive](#6-frontend-deep-dive)
7. [Data & Request Flow](#7-data--request-flow)
8. [Environment Variables](#8-environment-variables)
9. [Security Design](#9-security-design)
10. [Supported File Formats](#10-supported-file-formats)
11. [Study Tools](#11-study-tools)
12. [Performance Characteristics](#12-performance-characteristics)
13. [Extending LUMINA](#13-extending-LUMINA)

---

## 1. Project Overview

LUMINA is a **zero-cloud, zero-API-key** educational assistant. It runs entirely on the user's local machine:

| Property | Value |
|---|---|
| **Server** | Pure-Python `ThreadingHTTPServer` (stdlib) |
| **LLM** | Any HuggingFace `text-generation` model (loaded locally via `transformers`) |
| **Embedding** | Any HuggingFace sentence-transformer model (loaded via `sentence-transformers`) |
| **Vector Store** | ChromaDB (persisted to disk at `./chroma.db/`) |
| **RAG Framework** | LangChain (orchestration only; no OpenAI dependency) |
| **Frontend** | Vanilla HTML + CSS + JavaScript (no build step, no frameworks) |
| **Privacy** | All data — uploads, embeddings, chat history — stays on-device |

---

## 2. Key Features

- **Document Upload** — PDF, DOCX, PPTX, TXT, and Markdown files
- **Website Ingestion** — Paste any public URL; LUMINA fetches and indexes the page text
- **RAG Chat** — Ask natural-language questions answered from *your* source only
- **Quiz Generator** — Auto-generates 5 multiple-choice questions with answers
- **Flashcard Generator** — Produces 8–10 Q&A flashcards from key concepts
- **Summary Generator** — Creates a structured, section-by-section Markdown summary
- **SSRF Protection** — Website fetching is guarded against internal network probing
- **Lazy Loading + Caching** — Models and the vector store are loaded once per process (`lru_cache`)
- **Deduplication** — Re-uploading the same file does not re-index it in ChromaDB

---

## 3. High-Level Architecture

```
+--------------------------------------------------------------------+
|                          Browser (UI)                              |
|   web/index.html  .  web/app.js  .  web/*.css                     |
|                                                                    |
|   +--------------+  +--------------+  +----------------------+    |
|   |  Sidebar     |  |  Source Pane |  |  Chat Pane           |    |
|   |  (chat list) |  |  (document   |  |  (Q&A + study tools) |    |
|   |              |  |   preview)   |  |                      |    |
|   +--------------+  +--------------+  +----------------------+    |
+------------------------------+-----------------------------------------+
                               | HTTP (JSON over localhost:8000)
+------------------------------v-----------------------------------------+
|                     app.py  (LuminaHandler)                        |
|   ThreadingHTTPServer . CORS guard . SSRF guard . size limit       |
|                                                                    |
|  POST /api/upload    POST /api/website   POST /api/chat            |
|  POST /api/generate  GET /api/serve-file GET /api/source-content   |
+------------------------------+-----------------------------------------+
                               | Python function calls
+------------------------------v-----------------------------------------+
|                   backend/rag_pipeline.py                          |
|        index_document()  .  query()  .  generate_tool()           |
+---+---------------+---------------+---------------+----------------+
    |               |               |               |
    v               v               v               v
document_     embedding_       chroma_          llm_
loader.py     service.py       store.py         service.py
(load/split)  (HuggingFace     (ChromaDB        (HuggingFace
               Embeddings)      PersistentClient) Pipeline)
                                      |
                               retriever_
                               service.py
                               (MMR Search)
```

---

## 4. Directory Structure

```
LUMINA-/
|
+-- app.py                   # Entry point: HTTP server, API handlers, SSRF guard
|
+-- backend/                 # All AI/RAG logic; pure Python, no web framework
|   +-- config.py            # Centralised settings via .env
|   +-- document_loader.py   # File -> LangChain Document chunks
|   +-- models.py            # HuggingFace embedding and chat model loaders
|   +-- rag_pipeline.py      # Orchestrates indexing, retrieval, prompts, and tools
|
+-- web/                     # Static frontend served directly by LuminaHandler
|   +-- index.html           # Single-page app shell
|   +-- app.js               # All frontend logic (no framework)
|   +-- app.css              # All frontend styles
|
+-- uploads/                 # Runtime: uploaded files land here (git-ignored)
+-- chroma.db/               # Runtime: persisted vector embeddings (git-ignored)
|
+-- requirements.txt         # Python dependencies
+-- .env                     # Secrets/config (git-ignored; create manually)
+-- .gitignore
+-- Readme.md
+-- ARCHITECTURE.md          # This file
```

---

## 5. Backend Deep Dive

### 5.1 `app.py` — HTTP Server & API

**Class:** `LuminaHandler(SimpleHTTPRequestHandler)`

The server is intentionally dependency-free at the HTTP layer — no Flask, no FastAPI. Python's stdlib `ThreadingHTTPServer` is used so that each request gets its own thread (important when model inference is blocking).

#### API Endpoints

| Method | Path | Handler | Description |
|--------|------|---------|-------------|
| `GET` | `/*` | `do_GET` | Serves `web/` static files |
| `GET` | `/api/source-content` | `_handle_source_content` | Returns file content/URL for source pane preview |
| `GET` | `/api/serve-file` | `_handle_serve_file` | Streams raw file bytes (PDF viewer) |
| `POST` | `/api/upload` | `_save_upload` | Decodes base64 file, saves to `uploads/`, calls `index_document()` |
| `POST` | `/api/website` | `_save_website` | Fetches URL, extracts text, saves as `.txt`, calls `index_document()` |
| `POST` | `/api/chat` | `_ask_rag` | Calls `query(question, filepath)`, returns Markdown answer |
| `POST` | `/api/generate` | `_generate` | Calls `generate_tool(tool, filepath)` for quiz/flashcards/summary |

#### Security Controls

- **Allowlist** — Only the four POST paths are accepted; all others return 404.
- **Body size limit** — Requests over **35 MB** are rejected before reading.
- **Empty body guard** — Prevents `JSONDecodeError` on zero-byte reads.
- **Path traversal prevention** — `sourceId` is stripped to basename via `Path(...).name` before any file operation.
- **SSRF guard** (`_is_safe_url`) — Before fetching any URL, DNS resolves the hostname and rejects loopback, private (RFC 1918), link-local, reserved, and multicast IPs.

#### `_TextExtractor(HTMLParser)`

A lightweight HTML-to-text scraper (no BeautifulSoup dependency) that skips `<script>`, `<style>`, `<noscript>`, and `<svg>` tag trees entirely, joining visible text nodes with newlines. Pages yielding fewer than 100 characters are rejected.

---

### 5.2 `backend/config.py` — Centralised Settings

Uses a **frozen dataclass** (`Settings`) populated once at import time. The singleton `settings` object is imported by every backend module that needs configuration.

```python
settings = load_settings()   # called once at module import
```

Required variables raise `EnvironmentError` with a clear message if absent. Optional variables fall back to sensible defaults.

| Variable | Required | Default | Description |
|---|---|---|---|
| `LLM_MODEL` | Yes | — | HuggingFace model ID for the LLM |
| `EMBEDDING_MODEL` | Yes | — | HuggingFace model ID for embeddings |
| `CHROMA_PERSIST_DIR` | No | `./chroma.db` | Path where ChromaDB persists data |
| `RETRIEVER_K` | No | `3` | Number of chunks to retrieve per query |
| `RETRIEVER_LAMBDA_MULT` | No | `0.9` | MMR diversity (0 = max diversity, 1 = max relevance) |
| `CHUNK_SIZE` | No | `1000` | Characters per text chunk |
| `CHUNK_OVERLAP` | No | `150` | Overlap between consecutive chunks |

---

### 5.3 `backend/document_loader.py` — Ingestion

Turns a file on disk into a list of LangChain `Document` chunks.

#### Loader Map

| Extension | LangChain Loader | Notes |
|---|---|---|
| `.pdf` | `PyPDFLoader` | Loads pages individually and preserves page/page_label metadata |
| `.txt` | `TextLoader` | UTF-8 plain text |
| `.docx` / `.doc` | `Docx2txtLoader` | Strips DOCX XML, extracts raw text |
| `.pptx` / `.ppt` | `UnstructuredPowerPointLoader` | Extracts slide text |
| `.md` | `UnstructuredMarkdownLoader` | Markdown-aware parsing |

#### Chunking Strategy

`RecursiveCharacterTextSplitter` with a priority separator list:

```python
separators = ["\n\n", "\n", ". ", "? ", "! ", "; ", ", ", " ", ""]
```

This keeps paragraphs together before falling back to sentence boundaries and finally character-level splitting. Each chunk is tagged with `metadata["source"] = basename(filepath)` for per-document filtering.

---

### 5.4 `backend/models.py` — Embeddings & Local LLM

```python
@lru_cache(maxsize=4)
def get_embedding_model(model_name: str) -> HuggingFaceEmbeddings:
```

Loads any HuggingFace sentence-transformer model by ID. `lru_cache` ensures the embedding model is loaded **once per process** and reused across requests.

```python
@lru_cache(maxsize=2)
def get_chat_model(model_id: str) -> ChatHuggingFace:
```

Loads a HuggingFace text-generation pipeline and wraps it in `ChatHuggingFace`. The pipeline uses `return_full_text=False`, `max_new_tokens=1536`, and a moderate temperature setting so the model stays coherent while respecting format instructions.

---

### 5.5 `backend/rag_pipeline.py` — Orchestration, Storage & Tools

This module coordinates the whole RAG stack:

- `index_document()` loads and splits a file, creates embeddings, and stores chunks in ChromaDB.
- `query()` retrieves relevant chunks, builds a context prompt, and asks the chat model for an answer.
- `generate_tool()` retrieves broader context and generates quizzes, flashcards, summaries, glossary tables, or comparisons.

The file also contains the Chroma store singleton (`get_or_create_store()`), deduplicated indexing (`add_documents()`), retrieval logic (`_retrieve()`), prompt templates, and post-processing for tool outputs.

**Request flow:**
- `app.py` accepts uploads, website URLs, chat questions, and tool generation requests.
- `rag_pipeline.py` ensures the source is indexed and uses the stored embeddings to answer or generate tool content.
- The frontend receives Markdown responses and renders them in the chat workspace.

---

## 6. Frontend Deep Dive

The frontend is a **single-page application** with no build pipeline — just static HTML, CSS, and vanilla JavaScript served directly by `LuminaHandler`.

### Views

| View | Element | Shown when |
|---|---|---|
| Home | `#home` | No chat is active |
| Workspace | `#workspace` | A chat is open with a source |

### Workspace Layout (3-column)

```
+-------------+------------------+----------------+
|   Sidebar   |   Source Pane    |   Chat Pane    |
|  (chat list |  (document/URL   |  (Q&A thread + |
|  + search)  |   preview)       |  composer)     |
+-------------+------------------+----------------+
```

### Modals

| Modal ID | Purpose |
|---|---|
| `#newChatModal` | Choose between file upload or website URL |
| `#loadingModal` | Progress bar shown while uploading/indexing |
| `#toolModal` | Displays quiz / flashcard / summary results |

### `web/app.js` Key Responsibilities

- **State management** — single `state` object (`chatId`, `sourceId`, `sourceName`, `chatTitle`)
- **Chat persistence** — stored in `localStorage` so chats survive page reloads
- **File upload** — reads file as Base64, POSTs to `/api/upload`
- **Website ingestion** — POSTs URL to `/api/website`
- **Chat** — POSTs question + sourceId to `/api/chat`, renders Markdown via `marked.js`
- **Study tools** — POSTs tool name + sourceId to `/api/generate`, renders in `#toolModal`
- **Source preview** — GETs `/api/source-content?sourceId=...` and renders PDF, text, or website info
- **Progress simulation** — animated progress bar updates during long upload/index operations

### CSS Files

| File | Scope |
|---|---|
| `app.css` | All frontend styling for landing page, workspace, chat, and modals |

---

## 7. Data & Request Flow

### File Upload Flow

```
User selects file
       |
       v
app.js: readAsDataURL -> base64
       |
       v
POST /api/upload  { name, content (base64) }
       |
       v
app.py: _save_upload()
  +- Validate extension (.pdf/.docx/.pptx/.txt/.md)
  +- Decode base64 -> bytes
  +- Save to uploads/{uuid}_{name}
  +- index_document(filepath)
         |
         v
  rag_pipeline.index_document()
    +- load_and_split() -> chunks[]
    +- get_embedding_model() -> embed
    +- get_or_create_store() -> chroma
    +- add_documents() -> dedup + insert
       |
       v
{ sourceId, name }  <- returned to frontend
```

### Chat Query Flow

```
User types question -> Submit
       |
       v
POST /api/chat  { question, sourceId }
       |
       v
app.py: _ask_rag()
  +- query(question, filepath)
         |
         v
  rag_pipeline.query()
    +- index_document()         (no-op if already indexed)
    +- MMR search -> top-k docs
    +- Build context string
    +- PROMPT | LLM | StrOutputParser -> Markdown answer
       |
       v
{ answer: "## Markdown..." }  <- rendered by marked.js
```

---

## 8. Environment Variables

Create a `.env` file in the project root:

```env
# Required
LLM_MODEL=your-huggingface-model-id
# Example: microsoft/Phi-3-mini-4k-instruct

EMBEDDING_MODEL=your-embedding-model-id
# Example: BAAI/bge-small-en-v1.5

# Optional (all have defaults)
CHROMA_PERSIST_DIR=./chroma.db
RETRIEVER_K=3
RETRIEVER_LAMBDA_MULT=0.9
CHUNK_SIZE=1000
CHUNK_OVERLAP=150
PORT=8000
```

---

## 9. Security Design

| Threat | Mitigation |
|---|---|
| SSRF via website fetch | `_is_safe_url()` resolves DNS and blocks all private/loopback/reserved IPs |
| Path traversal on file serve | `sourceId` sanitised with `Path(...).name` (basename only) |
| Oversized uploads | Body rejected if `Content-Length > 35 MB` |
| Unsupported file types | Extension allowlist enforced before writing to disk |
| Empty request bodies | `Content-Length <= 0` raises `ValueError` before JSON parse |
| Unauthorized API paths | Only four POST paths in `_ALLOWED_PATHS`; all others get 404 |

> LUMINA is designed for **local, single-user use**. It binds to `127.0.0.1` only and is not reachable from the network by default.

---

## 10. Supported File Formats

| Format | Extension(s) | Loader |
|---|---|---|
| PDF | `.pdf` | `PyPDFLoader` |
| Word Document | `.docx`, `.doc` | `Docx2txtLoader` |
| PowerPoint | `.pptx`, `.ppt` | `UnstructuredPowerPointLoader` |
| Plain Text | `.txt` | `TextLoader` |
| Markdown | `.md` | `UnstructuredMarkdownLoader` |
| Website | URL | Custom `_TextExtractor` (stdlib `HTMLParser`) |

---

## 11. Study Tools

| Tool | Output | Details |
|---|---|---|
| **Quiz** | 5 MCQ questions (A-D) with `**Answer: [letter]**` | Tests conceptual understanding |
| **Flashcards** | 8-10 `**Q:**` / `**A:**` pairs | Covers key definitions and facts |
| **Summary** | Structured Markdown with `##` / `###` headings | Ends with a Key Takeaways section |

All tools use the same `generate_tool()` function with a generic retrieval query (`"key concepts, definitions, and main topics"`) to maximise topical coverage.

---

## 12. Performance Characteristics

| Operation | First Run | Subsequent Runs |
|---|---|---|
| Model loading (LLM) | 1-5 min (download + load) | Instant (lru_cache) |
| Embedding model load | 10-60 sec | Instant (lru_cache) |
| Document indexing (~10 page PDF) | 5-30 sec | Instant (dedup check) |
| RAG query | 5-60 sec (depends on LLM size) | Same (no caching of answers) |
| Study tool generation | 10-90 sec | Same |

HuggingFace models are cached in `~/.cache/huggingface/` after the first download.

---

## 13. Extending LUMINA

### Swap the LLM
Change `LLM_MODEL` in `.env` to any HuggingFace `text-generation` model. No code changes required.

### Swap the Embedding Model
Change `EMBEDDING_MODEL` in `.env`. If you change the model after indexing, delete `chroma.db/` and re-index — embedding dimensions may differ between models.

### Add a New File Format
1. Add the loader import in `backend/document_loader.py`
2. Add the extension → loader class mapping to `LOADER_MAP`
3. Add the extension to the allowlist regex in `app.py` (`_save_upload`)
4. Optionally add preview handling in `_handle_source_content`

### Add a New Study Tool
1. Define a new `ChatPromptTemplate` in `backend/rag_pipeline.py`
2. Add it to the `TOOL_PROMPTS` dict
3. Add the tool name to the allowlist in `app.py` (`_generate`)
4. Add a button in `web/index.html` with `data-tool="your-tool"`

### Replace ChromaDB with FAISS
Only `backend/rag_pipeline.py` needs to change — it contains the Chroma store, retrieval, and indexing logic. The rest of the code interacts through the same high-level functions, so no other modules need provider-specific edits.
