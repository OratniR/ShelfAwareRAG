import datetime as dt
from pathlib import Path

# 日本標準時。
# 日本はサマータイムが無くオフセットが一定なので、tzdata に依存しない固定オフセットで正しく扱える
# （コンテナに /usr/share/zoneinfo が無い環境でも ZoneInfoNotFoundError にならない）。
JST = dt.timezone(dt.timedelta(hours=9), "JST")

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

# --- 賞味期限推定ステータス (items.is_estimated) ---
# 0: 未処理 / 1: 推定済 / 2: 対象外(食品ではない) / 3: 失敗(要再試行)
STATUS_UNPROCESSED = 0
STATUS_ESTIMATED = 1
STATUS_NON_FOOD = 2
STATUS_FAILED = 3

# ダッシュボード表示用ラベル (dashboard.py と scheduler.py で共用)
STATUS_LABELS = {
    STATUS_UNPROCESSED: "🕒 未処理",
    STATUS_ESTIMATED: "✅ 推定済",
    STATUS_NON_FOOD: "🚫 対象外",
    STATUS_FAILED: "⚠️ 失敗",
}

# 再推定の対象外とするステータス（推定が確定したもの）
STATUS_FINALIZED = (STATUS_ESTIMATED, STATUS_NON_FOOD)
