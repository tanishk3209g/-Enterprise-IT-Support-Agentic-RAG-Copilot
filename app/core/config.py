from functools import lru_cache
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[2]

class Settings(BaseSettings):
    app_name: str = "Enterprise IT Support Agentic RAG Copilot"
    app_env: str = "development"

    # LLM Provider selection: "groq" or "gemini"
    llm_provider: str = "groq"

    # Groq settings (free tier available at console.groq.com)
    groq_api_key: str = ""
    groq_model: str = "openai/gpt-oss-20b"

    # Google Gemini settings (free tier available at aistudio.google.com)
    gemini_api_key: str = ""
    gemini_model: str = "gemini-1.5-flash"

    # Vector DB settings (Open-source local ChromaDB)
    chroma_db_dir: str = str(BASE_DIR / "data" / "chroma_db")
    chroma_collection_name: str = "company_it_kb"

    # Embeddings (Free Google Gemini Embeddings - zero disk space, fast)
    embedding_model: str = "models/gemini-embedding-001"

    # RAG pipeline settings
    top_k: int = 4
    max_retries: int = 1
    admin_api_key: str = "change-me"

    # Storage paths
    audit_db_path: str = str(BASE_DIR / "data" / "audit.db")
    upload_dir: str = str(BASE_DIR / "uploads")
    sample_kb_dir: str = str(BASE_DIR / "data" / "sample_kb")

    model_config = SettingsConfigDict(env_file=str(BASE_DIR / ".env"), extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
