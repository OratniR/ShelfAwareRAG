from pydantic_settings import BaseSettings
from pydantic import Field

class Settings(BaseSettings):
    DB_PATH: str = "data/inventory.db"
    EMBEDDING_MODEL: str = "intfloat/multilingual-e5-small"
    LLM_API_BASE: str = Field(default="http://localhost:8001/v1")
    LLM_API_KEY: str = "unused"
    LLM_MODEL: str = "open-calm-3b"

    class Config:
        env_file = ".env" # This tells it to load from a .env file
        extra = "ignore"

# Create a single instance that the rest of your app can import
settings = Settings()