from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    UPLOADS_DIR: str = str(Path(__file__).resolve().parents[3] / "uploads")
    DOCSITE_ASSETS_DIR: str = str(Path(__file__).resolve().parents[3] / "docsite_assets")
    DOCSITE_INDEX_DIR: str = str(Path(__file__).resolve().parents[3] / "search_index_docsites")
    SEARCH_INDEX_DIR: str = str(Path(__file__).resolve().parents[3] / "search_index")
    KNOWLEDGE_INDEX_DIR: str = str(Path(__file__).resolve().parents[3] / "search_index_knowledge")
    DOCUMENT_INDEX_DIR: str = str(Path(__file__).resolve().parents[3] / "search_index_documents")
    MONGO_URI: str = "mongodb://localhost:27017"
    MONGO_DBNAME: str = "claude_todo"

    REDIS_URI: str = "redis://localhost:6379/0"
    REDIS_MCP_URI: str = "redis://localhost:6379/1"

    SECRET_KEY: str = "change-me"
    REFRESH_SECRET_KEY: str = "change-me-refresh"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""

    BASE_URL: str = ""

    FRONTEND_URL: str = "http://localhost:3000"

    WEBAUTHN_RP_ID: str = "localhost"
    WEBAUTHN_RP_NAME: str = "MCP Todo"
    WEBAUTHN_ORIGIN: str = "http://localhost:3000"

    INIT_ADMIN_EMAIL: str = ""
    INIT_ADMIN_PASSWORD: str = ""

    model_config = {"env_file": ("../.env", ".env"), "extra": "ignore"}


settings = Settings()
