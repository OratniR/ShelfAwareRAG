# tests/integration/test_estimation_flow.py
"""
実DB(SQLite) + 実Estimator の統合テスト。

LLMサーバーと BRAVE_API_KEY が必要。無い環境では自動でスキップされる。

rag-api コンテナには pytest と tests/ が入っていない（api ステージは `--no-dev` かつ
`COPY src/` のみ）ため、benchmark ステージで実行する:

    docker compose run --rm -v "$PWD/tests:/app/tests" benchmark \\
        uv run pytest tests/integration/test_estimation_flow.py -v -s -m integration
"""

import pytest

from shelf_aware.constants import STATUS_ESTIMATED, STATUS_FAILED, STATUS_NON_FOOD, STATUS_UNPROCESSED
from tests._llm import llm_server_available
from tests._stubs import install_heavy_stubs

install_heavy_stubs()

from shelf_aware.config import settings  # noqa: E402
from shelf_aware.database import InventoryDAO  # noqa: E402
from shelf_aware.estimation import (  # noqa: E402
    ExpirationEstimator,
    apply_estimation_outcome,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def dao(tmp_path, monkeypatch):
    """実データを壊さないよう、テスト用DBへ差し替える。"""
    monkeypatch.setattr("shelf_aware.database.SQLITE_DB_PATH", tmp_path / "inventory.db")
    monkeypatch.setattr("shelf_aware.database.CHROMA_DB_DIR", tmp_path / "chroma_db")
    instance = InventoryDAO(use_chroma=False)
    yield instance
    instance.conn.close()


@pytest.mark.asyncio
async def test_estimation_always_sets_terminal_status(dao):
    """
    推定を実行したら、ステータスは必ず確定する（未処理のまま残らない）。

    これが今回の障害（検索は成功しているのにダッシュボードが未処理のまま）の回帰テスト。
    """
    if not settings.BRAVE_API_KEY:
        pytest.skip("BRAVE_API_KEY is not set")
    if not await llm_server_available():
        pytest.skip(f"LLM server is not reachable at {settings.LLM_API_BASE}")

    item_name = "豆板醤"
    dao.add_or_update_item(item_name, "冷蔵庫")

    estimator = ExpirationEstimator()
    outcome = await estimator.estimate_expiration(item_name, dao)
    apply_estimation_outcome(dao, item_name, outcome)

    row = dao.get_item(item_name)
    print(f"\n--- outcome: {outcome.as_dict()}")
    print(f"--- db row : is_estimated={row['is_estimated']} expiry={row['expiry_date']} error={row['last_error']}")

    assert row["is_estimated"] in (STATUS_ESTIMATED, STATUS_NON_FOOD, STATUS_FAILED), (
        f"ステータスが未処理のまま残っている: outcome={outcome.as_dict()}"
    )
    if row["is_estimated"] == STATUS_FAILED:
        assert row["last_error"], "失敗として記録する場合は last_error が必須"


@pytest.mark.asyncio
async def test_estimation_skipped_keeps_status_untouched(dao):
    """Braveキーが無い場合は「実行しなかった」扱いでDBを変更しない。"""
    item_name = "豆板醤"
    dao.add_or_update_item(item_name, "冷蔵庫")

    estimator = ExpirationEstimator()
    estimator.brave_api_key = ""  # クォータ切れ等をシミュレート

    outcome = await estimator.estimate_expiration(item_name, dao)
    apply_estimation_outcome(dao, item_name, outcome)

    assert outcome.status.value == "skipped"
    assert dao.get_item(item_name)["is_estimated"] == STATUS_UNPROCESSED
