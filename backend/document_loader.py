import logging
import os

from langchain_community.document_loaders import (
    TextLoader,
    PyPDFLoader,
    UnstructuredMarkdownLoader,
    UnstructuredPowerPointLoader,
    Docx2txtLoader,
)
from langchain_text_splitters import RecursiveCharacterTextSplitter

logger = logging.getLogger(__name__)

LOADER_MAP = {
    ".pdf": PyPDFLoader,
    ".txt": TextLoader,
    ".docx": Docx2txtLoader,
    ".doc": Docx2txtLoader,
    ".pptx": UnstructuredPowerPointLoader,
    ".ppt": UnstructuredPowerPointLoader,
    ".md": UnstructuredMarkdownLoader,
}


def load_document(filepath: str):
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"File not found: {filepath}")

    extension = os.path.splitext(filepath)[1].lower()
    loader_cls = LOADER_MAP.get(extension)
    if loader_cls is None:
        raise ValueError(f"Unsupported file type: {extension}")

    logger.info("Loading document: %s", filepath)
    loader = loader_cls(filepath)
    docs = loader.load()

    # Tag each chunk with its source filename for citation later.
    for doc in docs:
        doc.metadata["source"] = os.path.basename(filepath)

    return docs


def split(documents, chunk_size: int = 1000, chunk_overlap: int = 150):
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=["\n\n", "\n", ". ", "? ", "! ", "; ", ", ", " ", ""],
    )
    return splitter.split_documents(documents)


def load_and_split(filepath: str, chunk_size: int = 1000, chunk_overlap: int = 150):
    docs = load_document(filepath)
    return split(docs, chunk_size=chunk_size, chunk_overlap=chunk_overlap)