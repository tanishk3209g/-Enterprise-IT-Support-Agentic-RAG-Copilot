import os
from pathlib import Path
from langchain_chroma import Chroma
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from app.core.config import get_settings

settings = get_settings()

_embeddings = None
_vectorstore = None


class SafeGoogleGenerativeAIEmbeddings(GoogleGenerativeAIEmbeddings):
    """Google Gemini embeddings with safeguard against empty query strings."""
    def embed_query(self, text: str) -> list[float]:
        cleaned = (text or "").strip()
        if not cleaned:
            cleaned = "support"
        return super().embed_query(cleaned)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        cleaned = [(t or "").strip() or "support" for t in texts]
        return super().embed_documents(cleaned)


def get_embeddings():
    """Returns free Google Gemini embeddings (fast, zero local model download)."""
    global _embeddings
    if _embeddings is None:
        if not settings.gemini_api_key:
            raise RuntimeError("GEMINI_API_KEY is missing in your .env file")
        _embeddings = SafeGoogleGenerativeAIEmbeddings(
            model=settings.embedding_model,
            google_api_key=settings.gemini_api_key,
        )
    return _embeddings


def get_vectorstore():
    """Initializes and returns local persistent Chroma vector store."""
    global _vectorstore
    if _vectorstore is None:
        os.makedirs(settings.chroma_db_dir, exist_ok=True)
        _vectorstore = Chroma(
            collection_name=settings.chroma_collection_name,
            embedding_function=get_embeddings(),
            persist_directory=settings.chroma_db_dir,
        )
    return _vectorstore


def get_retriever():
    return get_vectorstore().as_retriever(search_kwargs={"k": settings.top_k})


def add_documents(chunks):
    store = get_vectorstore()
    return store.add_documents(chunks)
