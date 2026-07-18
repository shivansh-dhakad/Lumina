# LUMINA — Local AI Learning Assistant

**LUMINA** is a fully local, private AI-powered study assistant. Upload any document or paste a website URL and instantly chat with an AI that answers questions grounded *exclusively* in your source — no API keys, no cloud, no data leaving your machine.

---

## Quick Start

### 1. Clone & create your environment

```powershell
git clone <repo-url>
cd SIRIUS-
python -m venv venv
```

### 2. Install dependencies

```powershell
.\venv\Scripts\pip.exe install -r requirements.txt
```

> **Note:** The first install downloads `torch`, `transformers`, and `sentence-transformers` — this can take several minutes and a few GB of disk space.

### 3. Configure your `.env`

Create a `.env` file in the project root:

```env
# Required: HuggingFace model IDs (downloaded automatically on first run)
LLM_MODEL=microsoft/Phi-3-mini-4k-instruct
EMBEDDING_MODEL=BAAI/bge-small-en-v1.5

# Optional (these defaults work for most setups)
CHROMA_PERSIST_DIR=./chroma.db
RETRIEVER_K=3
RETRIEVER_LAMBDA_MULT=0.9
CHUNK_SIZE=1000
CHUNK_OVERLAP=150
PORT=8000
```

### 4. Run SIRIUS

```powershell
.\venv\Scripts\python.exe app.py
```

Open **http://127.0.0.1:8000** in your browser.

---

## What SIRIUS Can Do

| Feature | Description |
|---|---|
| **Document Chat** | Upload PDF, DOCX, PPTX, TXT, or Markdown — then ask questions |
| **Website Chat** | Paste any public URL — SIRIUS indexes the page text |
| **Quiz** | Auto-generates 5 multiple-choice questions from your source |
| **Flashcards** | Creates 8–10 Q&A flashcard pairs covering key concepts |
| **Summary** | Produces a structured Markdown summary with a Key Takeaways section |

---

## Supported File Types

| Format | Extension(s) |
|---|---|
| PDF | `.pdf` |
| Word Document | `.docx`, `.doc` |
| PowerPoint | `.pptx`, `.ppt` |
| Plain Text | `.txt` |
| Markdown | `.md` |
| Website | Any `http://` or `https://` URL |

---

## Project Structure

```
SIRIUS-/
├── app.py                   # HTTP server + API (no external web framework)
├── backend/
│   ├── config.py            # All settings loaded from .env
│   ├── document_loader.py   # File → text chunks (PDF, DOCX, PPTX, TXT, MD)
│   ├── embedding_service.py # HuggingFace embedding model (cached per process)
│   ├── chroma_store.py      # ChromaDB singleton with deduplication
│   ├── retriever_service.py # MMR retriever factory
│   ├── llm_service.py       # HuggingFace LLM (cached per process)
│   └── rag_pipeline.py      # Public API: index_document(), query(), generate_tool()
├── web/
│   ├── index.html           # Single-page app shell
│   ├── app.js               # All frontend logic
│   ├── style.css            # Home / landing styles
│   ├── workspace.css        # 3-column workspace layout
│   ├── chat.css             # Chat message styles
│   └── features.css         # Study tool modal styles
├── uploads/                 # Runtime: user files (git-ignored)
├── chroma.db/               # Runtime: vector embeddings (git-ignored)
├── requirements.txt
├── .env                     # Your secrets (create manually, git-ignored)
├── ARCHITECTURE.md          # Full architecture & technical reference
└── Readme.md                # This file
```

---

## Architecture Overview

SIRIUS uses a **Retrieval-Augmented Generation (RAG)** pipeline:

```
Browser  →  app.py (HTTP)  →  rag_pipeline.py
                                    |
             ┌──────────────────────┼──────────────────────┐
             ▼                      ▼                      ▼
    document_loader.py    embedding_service.py     llm_service.py
    (chunk the file)      (embed the chunks)       (generate answer)
                                    |
                             chroma_store.py
                             (persist & retrieve)
```

See [`ARCHITECTURE.md`](ARCHITECTURE.md) for a complete technical reference including all API endpoints, data flow diagrams, security design, and extension guides.

---

## Backend Module Map

| Module | File | Responsibility |
|---|---|---|
| Config | `backend/config.py` | Centralised env-var settings (frozen dataclass) |
| Loader | `backend/document_loader.py` | Read files off disk, split into chunks |
| Embeddings | `backend/embedding_service.py` | Load & cache the HuggingFace embedding model |
| Vector DB | `backend/chroma_store.py` | Open/reuse the Chroma store, dedup indexed sources |
| Retriever | `backend/retriever_service.py` | Configure MMR retrieval strategy |
| LLM | `backend/llm_service.py` | Load & cache the HuggingFace chat model |
| RAG Pipeline | `backend/rag_pipeline.py` | Orchestrates everything; public API surface |

To swap providers later (e.g. ChromaDB → FAISS, HuggingFace → Ollama), you only need to edit the one corresponding module — nothing else imports a provider SDK directly.

---

## Environment Variables Reference

| Variable | Required | Default | Description |
|---|---|---|---|
| `LLM_MODEL` | **Yes** | — | HuggingFace model ID for text generation |
| `EMBEDDING_MODEL` | **Yes** | — | HuggingFace sentence-transformer model ID |
| `CHROMA_PERSIST_DIR` | No | `./chroma.db` | ChromaDB storage path |
| `RETRIEVER_K` | No | `3` | Number of chunks retrieved per query |
| `RETRIEVER_LAMBDA_MULT` | No | `0.9` | MMR diversity (0 = max diversity, 1 = max relevance) |
| `CHUNK_SIZE` | No | `1000` | Characters per text chunk |
| `CHUNK_OVERLAP` | No | `150` | Character overlap between chunks |
| `PORT` | No | `8000` | HTTP server port |

---

## Security Notes

- SIRIUS binds to `127.0.0.1` only — it is **not** accessible from other machines on your network.
- Website fetching includes an **SSRF guard** that blocks requests to private, loopback, and reserved IP ranges.
- Uploaded files are saved with a random UUID prefix; direct path traversal is prevented at the API layer.
- File uploads are limited to **35 MB** and restricted to the allowlisted extensions above.

---

## Performance Tips

- **First run is slow** — HuggingFace models are downloaded (~500 MB–5 GB depending on the model) and then loaded into RAM/VRAM. Subsequent starts are much faster.
- **GPU acceleration** — If you have a CUDA-capable GPU, `torch` will use it automatically, significantly speeding up embedding and inference.
- **Model size trade-off** — Smaller models (e.g. Phi-3-mini) are faster but less accurate. Larger models (e.g. Mistral-7B) give better answers but require more RAM.
- **Re-indexing** — Documents are deduplicated in ChromaDB; re-uploading the same file is a no-op.

---

## Troubleshooting

| Problem | Solution |
|---|---|
| `EnvironmentError: Missing required environment variable: LLM_MODEL` | Check that `.env` exists in the project root with both `LLM_MODEL` and `EMBEDDING_MODEL` set |
| Server starts but chat returns errors | Check the terminal for Python tracebacks; usually a missing dependency or bad model ID |
| Slow first response | Normal — the model is being loaded into memory for the first time |
| `chroma.db` grows large | Safe to delete; it will be rebuilt from `uploads/` on next use |
| Changed `EMBEDDING_MODEL` but getting errors | Delete `chroma.db/` — embedding dimensions differ between models |

---

## License

This project is for personal and educational use. See repository for license details.
