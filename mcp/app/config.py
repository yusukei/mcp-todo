from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    BACKEND_URL: str = "http://localhost:8000"
    MCP_INTERNAL_SECRET: str = "change-me"
    REDIS_MCP_URI: str = "redis://localhost:6379/1"

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
