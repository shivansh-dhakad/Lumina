from base64 import b64decode
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import ipaddress
import json
import logging
import os
import re
import socket
import uuid
from html.parser import HTMLParser
from urllib.parse import urlparse, parse_qs
from urllib.request import Request, urlopen

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(name)s  %(message)s")
logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent
WEB_ROOT = ROOT / "web"
UPLOAD_ROOT = ROOT / "uploads"
UPLOAD_ROOT.mkdir(exist_ok=True)

_SKIP_TAGS = {"script", "style", "noscript", "svg"}
_ALLOWED_PATHS = {"/api/upload", "/api/website", "/api/chat", "/api/generate", "/api/suggest"}


def _is_safe_url(url: str) -> bool:
    """SSRF guard: return False if the host resolves to a private/loopback/reserved IP.

    This prevents an attacker from using the website-fetch endpoint to probe
    internal services (e.g. 127.0.0.1, 192.168.x.x, cloud metadata endpoints).
    """
    try:
        hostname = urlparse(url).hostname or ""
        ip = ipaddress.ip_address(socket.gethostbyname(hostname))
        return not (
            ip.is_loopback
            or ip.is_private
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
        )
    except (socket.gaierror, ValueError):
        # If DNS resolution fails, block the request.
        return False


class LuminaHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_ROOT), **kwargs)
    def do_OPTIONS(self):
        """Handle pre-flight CORS requests so a dev-server frontend works."""
        self.send_response(204)
        self._add_cors_headers()
        self.end_headers()

    def _add_cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "http://127.0.0.1:8000")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/source-content":
            self._handle_source_content(parsed.query)
        elif parsed.path == "/api/serve-file":
            self._handle_serve_file(parsed.query)
        else:
            super().do_GET()

    def _handle_serve_file(self, query_str):
        params = parse_qs(query_str)
        source_id = params.get("sourceId", [""])[0]
        source_id = Path(source_id).name
        filepath = UPLOAD_ROOT / source_id
        if not filepath.exists() or not filepath.is_file():
            self.send_error(404, "File not found")
            return
        try:
            content = filepath.read_bytes()
            self.send_response(200)
            self._add_cors_headers()
            ext = filepath.suffix.lower()
            if ext == ".pdf":
                self.send_header("Content-Type", "application/pdf")
            elif ext == ".txt":
                self.send_header("Content-Type", "text/plain; charset=utf-8")
            elif ext == ".md":
                self.send_header("Content-Type", "text/markdown; charset=utf-8")
            else:
                self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        except Exception as error:
            logger.exception("Serve file failed")
            self.send_error(500, str(error))

    def _handle_source_content(self, query_str):
        params = parse_qs(query_str)
        source_id = params.get("sourceId", [""])[0]
        source_id = Path(source_id).name
        filepath = UPLOAD_ROOT / source_id
        if not filepath.exists() or not filepath.is_file():
            self.send_error(404, "File not found")
            return
        try:
            ext = filepath.suffix.lower()
            if ext == ".pdf":
                response = {
                    "type": "pdf",
                    "url": f"/api/serve-file?sourceId={source_id}"
                }
            elif "website" in filepath.name:
                content = filepath.read_text(encoding="utf-8", errors="replace")
                url = ""
                text = content
                if content.startswith("Source URL: "):
                    lines = content.split("\n", 2)
                    url = lines[0].replace("Source URL: ", "").strip()
                    if len(lines) > 2:
                        text = lines[2].strip()
                response = {
                    "type": "website",
                    "url": url,
                    "content": text
                }
            elif ext in {".txt", ".md"}:
                content = filepath.read_text(encoding="utf-8", errors="replace")
                response = {
                    "type": ext[1:],
                    "content": content
                }
            elif ext in {".docx", ".doc", ".pptx", ".ppt"}:
                from backend.document_loader import load_document
                docs = load_document(str(filepath))
                content = "\n\n".join(doc.page_content for doc in docs)
                response = {
                    "type": "text",
                    "content": content
                }
            else:
                response = {
                    "type": "unknown",
                    "content": "Preview is not supported for this file type."
                }
            self._json(200, response)
        except Exception as error:
            logger.exception("Get source content failed")
            self._json(500, {"error": str(error)})

    def do_POST(self):
        if self.path not in _ALLOWED_PATHS:
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            # Block empty bodies — prevents JSONDecodeError on zero-byte reads.
            if length <= 0:
                raise ValueError("Request body is required.")
            # Enforce the size limit on the *declared* length before reading.
            if length > 35 * 1024 * 1024:
                raise ValueError("Files must be smaller than 35 MB.")
            payload = json.loads(self.rfile.read(length))
            if self.path == "/api/upload":
                response = self._save_upload(payload)
            elif self.path == "/api/website":
                response = self._save_website(payload)
            elif self.path == "/api/generate":
                response = self._generate(payload)
            elif self.path == "/api/suggest":
                response = self._suggest(payload)
            else:
                response = self._ask_rag(payload)
            self._json(200, response)
        except Exception as error:
            logger.exception("POST %s failed", self.path)
            self._json(400, {"error": str(error)})

    def _resolve_source_paths(self, payload) -> list[str]:
        """Resolve one or more uploaded source IDs to absolute file paths."""
        source_ids = payload.get("sourceIds")
        if source_ids is None:
            single = payload.get("sourceId", "")
            source_ids = [single] if single else []
        if not isinstance(source_ids, list):
            source_ids = [source_ids]

        paths: list[str] = []
        for source_id in source_ids:
            basename = Path(str(source_id)).name
            if not basename:
                continue
            filepath = UPLOAD_ROOT / basename
            if filepath.exists() and filepath.is_file():
                paths.append(str(filepath))
        return paths

    def _save_upload(self, payload):
        name = Path(payload.get("name", "source.txt")).name
        if not re.search(r"\.(pdf|docx|pptx|txt|md)$", name, re.I):
            raise ValueError("Unsupported source type.")
        content = payload.get("content", "")
        if not content:
            raise ValueError("The selected file is empty.")
        destination = UPLOAD_ROOT / f"{uuid.uuid4().hex}_{name}"
        destination.write_bytes(b64decode(content))
        # Index eagerly so the document is fully embedded before the chat
        # workspace opens — the loading modal is visible during this call.
        from backend.rag_pipeline import index_document
        index_document(str(destination))
        return {"sourceId": destination.name, "name": name}

    def _ask_rag(self, payload):
        question = str(payload.get("question", "")).strip()
        source_paths = self._resolve_source_paths(payload)
        if not question or not source_paths:
            raise ValueError("A question and an uploaded source are required.")
        history = payload.get("history") or []
        if not isinstance(history, list):
            history = []
        mode = str(payload.get("mode", "normal")).strip().lower()
        from backend.rag_pipeline import query
        return query(question, source_paths, history=history, mode=mode)

    def _suggest(self, payload):
        source_paths = self._resolve_source_paths(payload)
        if not source_paths:
            raise ValueError("An uploaded source is required.")
        from backend.rag_pipeline import suggest_questions
        return {"questions": suggest_questions(source_paths)}

    def _save_website(self, payload):
        url = str(payload.get("url", "")).strip()
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Enter a valid http or https website URL.")
        # SSRF guard: block requests to internal/loopback addresses.
        if not _is_safe_url(url):
            raise ValueError(
                "That URL points to a restricted or internal address and cannot be fetched."
            )
        request = Request(url, headers={"User-Agent": "SIRIUS local learning assistant"})
        with urlopen(request, timeout=15) as response:
            html = response.read(4 * 1024 * 1024).decode("utf-8", errors="replace")
        parser = _TextExtractor()
        parser.feed(html)
        text = parser.text.strip()
        if len(text) < 100:
            raise ValueError(
                "SIRIUS could not extract enough readable content from this website."
            )
        name = parsed.netloc + (parsed.path.rstrip("/") or "/")
        destination = UPLOAD_ROOT / f"{uuid.uuid4().hex}_website.txt"
        destination.write_text(f"Source URL: {url}\n\n{text}", encoding="utf-8")
        # Index eagerly — same rationale as _save_upload.
        from backend.rag_pipeline import index_document
        index_document(str(destination))
        return {"sourceId": destination.name, "name": name, "url": url}

    def _generate(self, payload):
        """Generate a quiz, flashcard set, or summary from an indexed document."""
        tool = str(payload.get("tool", "")).strip().lower()
        source_paths = self._resolve_source_paths(payload)
        if tool not in {"quiz", "flashcards", "summary", "glossary", "compare"}:
            raise ValueError("tool must be one of: quiz, flashcards, summary, glossary, compare")
        if not source_paths:
            raise ValueError("Source not found. Please re-upload your document.")
        topic = str(payload.get("topic", "")).strip() or None
        from backend.rag_pipeline import generate_tool
        return {"content": generate_tool(tool, source_paths, topic=topic)}

    def _json(self, status, body):
        encoded = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self._add_cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    # Silence default request logging to keep the console clean.
    def log_message(self, fmt, *args):
        logger.debug("HTTP %s", fmt % args)


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self._skip_stack: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip_stack.append(tag)

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS and tag in self._skip_stack:
            # Remove the most recent matching open tag from the stack.
            for i in range(len(self._skip_stack) - 1, -1, -1):
                if self._skip_stack[i] == tag:
                    self._skip_stack.pop(i)
                    break

    def handle_data(self, data):
        if not self._skip_stack:
            normalized = " ".join(data.split())
            if normalized:
                self.parts.append(normalized)

    @property
    def text(self) -> str:
        return "\n".join(self.parts)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    server = ThreadingHTTPServer(("127.0.0.1", port), LuminaHandler)
    print(f"SIRIUS is running at http://127.0.0.1:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
