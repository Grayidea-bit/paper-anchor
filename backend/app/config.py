from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # LLM (OpenAI-compatible; NVIDIA NIM by default)
    llm_base_url: str = "https://integrate.api.nvidia.com/v1"
    llm_api_key: str = ""
    llm_chat_model: str = "deepseek-ai/deepseek-v3.1"

    # Embedding — NIM requires input_type ("passage" | "query"), handled in llm.py
    embed_base_url: str = "https://integrate.api.nvidia.com/v1"
    embed_api_key: str = ""
    embed_model: str = "nvidia/nv-embedqa-e5-v5"
    embed_dim: int = 1024

    database_url: str = "postgresql+asyncpg://paper:paper@localhost:5432/paper_reader"

    upload_dir: str = "/data/uploads"
    max_upload_mb: int = 50
    answer_language: str = "zh-TW"


@lru_cache
def get_settings() -> Settings:
    return Settings()
