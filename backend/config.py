import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    llm_model: str
    embedding_model: str
    chroma_persist_dir: str
    retriever_k: int
    retriever_lambda_mult: float
    chunk_size: int
    chunk_overlap: int


def _require(name: str) -> str:
    """Read a required env var; raise a clear error if it is absent or empty."""
    value = os.getenv(name)
    if not value:
        raise EnvironmentError(
            f"Missing required environment variable: {name}. "
            f"Set it in your .env file."
        )
    return value


def load_settings() -> Settings:
    return Settings(
        llm_model=_require("LLM_MODEL"),
        embedding_model=_require("EMBEDDING_MODEL"),
        chroma_persist_dir=os.getenv("CHROMA_PERSIST_DIR", "./chroma.db"),
        retriever_k=int(os.getenv("RETRIEVER_K", "3")),
        retriever_lambda_mult=float(os.getenv("RETRIEVER_LAMBDA_MULT", "0.9")),
        chunk_size=int(os.getenv("CHUNK_SIZE", "1000")),
        chunk_overlap=int(os.getenv("CHUNK_OVERLAP", "150")),
    )


# Loaded once at import time; every module that needs config imports this.
settings = load_settings()
