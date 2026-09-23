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

    # --- 推定パイプラインの調整値 (Raspberry Pi 5 の実測に合わせて .env で上書き可能) ---
    # LLMへJSON出力を強制するか: "auto"(対応していれば使う) / "off"(使わない)
    LLM_JSON_MODE: str = "auto"
    # 日次バックフィルの実行時刻(時)と1回あたりの処理件数
    BACKFILL_HOUR: int = 3
    BACKFILL_BATCH_SIZE: int = 5
    BACKFILL_TIMEZONE: str = "Asia/Tokyo"
    # 同じアイテムを「失敗」として記録する上限回数（無限リトライでAPIを浪費しない）
    MAX_ESTIMATION_ATTEMPTS: int = 3

    class Config:
        env_file = ".env"  # This tells it to load from a .env file
        extra = "ignore"


# Create a single instance that the rest of your app can import
settings = Settings()
