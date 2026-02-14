from pathlib import Path

# プロジェクトのルートディレクトリ (ShelfAwareRAG/) を取得
# constants.py が src/shelf_aware/ にある前提
PROJECT_ROOT = Path(__file__).resolve().parents[2]
SIMILARITY_THRESHOLD = 0.7  # RAGのretrieval結果の閾値　これより低ければ，該当結果はないとする

# データディレクトリの設定
DATA_DIR = PROJECT_ROOT / "data"
SQLITE_DB_PATH = DATA_DIR / "inventory.db"
CHROMA_DB_DIR = DATA_DIR / "chroma_db"

# LLM設定（Phase 2以降でも再利用可能）
CHROMA_COLLECTION_NAME = "inventory"

# ディレクトリの自動生成（インポート時に一度だけ実行されるので安全）
DATA_DIR.mkdir(parents=True, exist_ok=True)

# --- Brave Search Exclusions ---
# ノイズになりやすいQ&Aサイトやまとめサイトを除外するクエリ
EXCLUDED_DOMAINS = [
    "chiebukuro.yahoo.co.jp",
    "okwave.jp",
    "oshiete.goo.ne.jp",
    "detail.chiebukuro.yahoo.co.jp",
    "komachi.yomiuri.co.jp",
]
