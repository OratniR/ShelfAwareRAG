# tests/unit/test_database.py
"""
InventoryDAO のスキーマ移行とステータス管理のテスト。

chromadb / sentence_transformers はローカルやCIに無いことがあるためスタブ化する
(use_chroma=False なので実際には使われない)。
"""

import sqlite3

import pytest

from shelf_aware.constants import (
    STATUS_ESTIMATED,
    STATUS_FAILED,
    STATUS_NON_FOOD,
    STATUS_UNPROCESSED,
)
from tests._stubs import install_heavy_stubs

install_heavy_stubs()

from shelf_aware.database import InventoryDAO  # noqa: E402 - スタブ化後にimportする必要がある


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    """実データを壊さないよう、テスト用DBへ差し替える。"""
    path = tmp_path / "inventory.db"
    monkeypatch.setattr("shelf_aware.database.SQLITE_DB_PATH", path)
    monkeypatch.setattr("shelf_aware.database.CHROMA_DB_DIR", tmp_path / "chroma_db")
    return path


@pytest.fixture
def dao(db_path):
    instance = InventoryDAO(use_chroma=False)
    yield instance
    instance.conn.close()


def _columns(conn) -> set:
    return {row[1] for row in conn.execute("PRAGMA table_info(items)")}


def _row(conn, item_id):
    conn.row_factory = sqlite3.Row
    cursor = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,))
    row = cursor.fetchone()
    return dict(row) if row else None


# --- スキーマ移行 ---
def test_migration_adds_columns_to_existing_db(db_path):
    """旧スキーマのDBを壊さずに新カラムが追加されること（Piの既存DBで重要）。"""
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE items (
            id TEXT PRIMARY KEY,
            location TEXT NOT NULL,
            updated_at TIMESTAMP NOT NULL,
            expiry_date TIMESTAMP,
            is_estimated INTEGER DEFAULT 0
        )
    """)
    conn.execute("INSERT INTO items VALUES ('醤油', '冷蔵庫', '2026-01-01T00:00:00', '2026-12-31', 1)")
    conn.commit()
    conn.close()

    dao = InventoryDAO(use_chroma=False)
    try:
        assert {"attempt_count", "last_error", "last_attempted_at", "created_at"} <= _columns(dao.conn)
        # 既存データが保持され、新カラムはデフォルト値で埋まる
        row = _row(dao.conn, "醤油")
        assert row["expiry_date"] == "2026-12-31"
        assert row["is_estimated"] == 1
        assert row["attempt_count"] == 0
        assert row["last_error"] is None
        # 初回登録日時は残っていないので updated_at で代用する
        assert row["created_at"] == "2026-01-01T00:00:00"
    finally:
        dao.conn.close()


def test_new_db_has_all_columns(dao):
    assert {
        "expiry_date",
        "is_estimated",
        "attempt_count",
        "last_error",
        "last_attempted_at",
        "created_at",
    } <= _columns(dao.conn)


# --- 再登録（新しい在庫）の扱い ---
def test_add_or_update_resets_estimation_for_fresh_stock(dao):
    """
    再登録は「新しく買った在庫」として扱い、賞味期限を推定し直す。

    古い賞味期限を残すと「登録日より前に賞味期限がくる」状態になるため。
    """
    dao.add_or_update_item("豆腐", "冷蔵庫")
    dao.update_expiry("豆腐", "2026-09-26")
    assert _row(dao.conn, "豆腐")["is_estimated"] == STATUS_ESTIMATED

    dao.add_or_update_item("豆腐", "冷蔵庫")  # 同じ場所でも再登録 = 新しい在庫

    row = _row(dao.conn, "豆腐")
    assert row["expiry_date"] is None
    assert row["is_estimated"] == STATUS_UNPROCESSED
    assert row["attempt_count"] == 0
    assert row["last_error"] is None


def test_add_or_update_keeps_created_at(dao):
    """created_at（初回登録日時）は再登録で変わらない。"""
    dao.add_or_update_item("豆腐", "冷蔵庫")
    created_at = _row(dao.conn, "豆腐")["created_at"]

    dao.add_or_update_item("豆腐", "パントリー")

    row = _row(dao.conn, "豆腐")
    assert row["created_at"] == created_at
    assert row["location"] == "パントリー"


# --- ステータス遷移 ---
def test_mark_estimation_failed_records_reason_and_attempts(dao):
    dao.add_or_update_item("豆板醤", "冷蔵庫")

    dao.mark_estimation_failed("豆板醤", "no_days_extracted")

    row = _row(dao.conn, "豆板醤")
    assert row["is_estimated"] == STATUS_FAILED
    assert row["attempt_count"] == 1
    assert row["last_error"] == "no_days_extracted"
    assert row["last_attempted_at"] is not None


def test_update_expiry_clears_previous_error(dao):
    dao.add_or_update_item("豆板醤", "冷蔵庫")
    dao.mark_estimation_failed("豆板醤", "llm_extraction_failed")

    dao.update_expiry("豆板醤", "2027-03-01")

    row = _row(dao.conn, "豆板醤")
    assert row["is_estimated"] == STATUS_ESTIMATED
    assert row["expiry_date"] == "2027-03-01"
    assert row["last_error"] is None
    assert row["attempt_count"] == 2


def test_mark_as_non_food(dao):
    dao.add_or_update_item("ドライヤー", "洗面台")
    dao.mark_as_non_food("ドライヤー")

    row = _row(dao.conn, "ドライヤー")
    assert row["is_estimated"] == STATUS_NON_FOOD
    assert row["last_error"] is None


# --- バックフィル対象の抽出 ---
def test_get_items_for_backfill_targets_unprocessed_and_failed(dao):
    dao.add_or_update_item("未処理", "棚")
    dao.add_or_update_item("失敗", "棚")
    dao.add_or_update_item("推定済", "棚")
    dao.add_or_update_item("対象外", "棚")
    dao.mark_estimation_failed("失敗", "no_days_extracted")
    dao.update_expiry("推定済", "2027-01-01")
    dao.mark_as_non_food("対象外")

    targets = [item["id"] for item in dao.get_items_for_backfill(limit=10, max_attempts=3)]

    assert targets == ["未処理", "失敗"]


def test_get_items_for_backfill_stops_retrying_after_max_attempts(dao):
    """無限リトライでBraveのクォータを浪費しないこと。"""
    dao.add_or_update_item("何度も失敗", "棚")
    for _ in range(3):
        dao.mark_estimation_failed("何度も失敗", "no_days_extracted")

    assert dao.get_items_for_backfill(limit=10, max_attempts=3) == []

    # ダッシュボードで「未処理」に戻せば再試行対象になる
    dao.update_item_state("何度も失敗", None, STATUS_UNPROCESSED)
    targets = dao.get_items_for_backfill(limit=10, max_attempts=3)
    assert [item["id"] for item in targets] == ["何度も失敗"]
    assert targets[0]["attempt_count"] == 0


def test_get_items_for_backfill_respects_limit(dao):
    for i in range(5):
        dao.add_or_update_item(f"item{i}", "棚")

    assert len(dao.get_items_for_backfill(limit=2, max_attempts=3)) == 2


# --- その他 ---
def test_get_item_returns_none_for_unknown(dao):
    assert dao.get_item("存在しない") is None


def test_update_item_state_does_not_reset_attempts_for_other_statuses(dao):
    dao.add_or_update_item("豆板醤", "棚")
    dao.mark_estimation_failed("豆板醤", "x")
    dao.mark_estimation_failed("豆板醤", "x")

    dao.update_item_state("豆板醤", "2027-01-01", STATUS_ESTIMATED)

    row = _row(dao.conn, "豆板醤")
    assert row["attempt_count"] == 2
    assert row["is_estimated"] == STATUS_ESTIMATED


def test_update_item_state_skips_unchanged_rows(dao):
    """
    ダッシュボードの保存は全行に対して呼ばれるため、変更が無い行の updated_at を
    書き換えない（「登録日より前に賞味期限」の混乱を防ぐ）。
    """
    dao.add_or_update_item("豆板醤", "棚")
    before = _row(dao.conn, "豆板醤")

    dao.update_item_state("豆板醤", None, STATUS_UNPROCESSED)  # 同じ値で保存

    after = _row(dao.conn, "豆板醤")
    assert after["updated_at"] == before["updated_at"]

    # 値が変われば更新される
    dao.update_item_state("豆板醤", "2027-01-01", STATUS_ESTIMATED)
    assert _row(dao.conn, "豆板醤")["expiry_date"] == "2027-01-01"


def test_brave_usage_counter(dao):
    assert dao.get_current_usage("brave_search") == 0
    assert dao.check_and_increment_usage("brave_search", 2) is True
    assert dao.check_and_increment_usage("brave_search", 2) is True
    assert dao.check_and_increment_usage("brave_search", 2) is False
    assert dao.get_current_usage("brave_search") == 2
