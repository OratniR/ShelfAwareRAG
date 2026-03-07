from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DB_PATH: str = "data/inventory.db"
    EMBEDDING_MODEL: str = "intfloat/multilingual-e5-small"
    LLM_API_BASE: str = Field(default="http://llm-server:8001/v1")
    LLM_API_KEY: str = "unused"
    LLM_MODEL: str = "gemma-2-2b-jpn-it-q2km.gguf"
    BRAVE_API_KEY: str = Field(default="")
    # Add Notion settings
    NOTION_API_KEY: str = Field(default="")
    NOTION_DATASOURCE_ID: str = Field(default="")
    NOTION_ITEM_PROPERTY_NAME: str = "Name"  # Name of the property holding the item name
    NOTION_CHECKBOX_PROPERTY_NAME: str = "購入済み"  # Name of the checkbox property
    # Langfuse settings (cloud.langfuse.comに接続、.envでキーを設定)
    LANGFUSE_SECRET_KEY: str = Field(default="")
    LANGFUSE_PUBLIC_KEY: str = Field(default="")
    LANGFUSE_HOST: str = Field(default="https://cloud.langfuse.com")

    class Config:
        env_file = ".env"  # This tells it to load from a .env file
        extra = "ignore"


# Create a single instance that the rest of your app can import
settings = Settings()
