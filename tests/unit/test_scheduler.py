# tests/unit/test_scheduler.py
"""日次バックフィルのスケジューラー配線のテスト（実際のジョブ実行はしない）。"""

import datetime as dt
from unittest.mock import AsyncMock, MagicMock

import pytest

from shelf_aware.config import settings
from shelf_aware.estimation import EstimationOutcome, EstimationResult
from tests._stubs import install_heavy_stubs

install_heavy_stubs()

from shelf_aware.scheduler import BackfillScheduler  # noqa: E402


@pytest.fixture
def scheduler() -> BackfillScheduler:
    dao = MagicMock()
    dao.get_current_usage.return_value = 0
    dao.get_items_for_backfill.return_value = []
    return BackfillScheduler(dao, MagicMock())


@pytest.mark.asyncio
async def test_scheduler_registers_daily_job(scheduler):
    scheduler.start()
    try:
        job = scheduler.scheduler.get_job("daily_backfill")
        assert job is not None
        assert job.next_run_time.hour == settings.BACKFILL_HOUR
        # コンテナのTZがUTCでもJST 3:00に動くこと
        assert dt.datetime.now(job.trigger.timezone).utcoffset() == dt.timedelta(hours=9)
    finally:
        scheduler.shutdown()


def test_timezone_falls_back_without_tzdata(monkeypatch):
    """
    tzdata が無いコンテナでも Asia/Tokyo を +09:00 固定で解決できること。

    (python:slim には /usr/share/zoneinfo が無いため、Dockerイメージに tzdata を
     追加せずに済ませるためのフォールバック)
    """
    import shelf_aware.scheduler as scheduler_module

    def _raise(_name):
        raise ModuleNotFoundError("No time zone found with key Asia/Tokyo")

    monkeypatch.setattr(scheduler_module, "ZoneInfo", _raise)

    resolved = scheduler_module._resolve_timezone()

    assert resolved.utcoffset(None) == dt.timedelta(hours=9)
    assert resolved.tzname(None) == "JST"


@pytest.mark.asyncio
async def test_backfill_survives_empty_targets(scheduler):
    await scheduler.run_backfill_job()  # 例外が出ないこと


@pytest.mark.asyncio
async def test_backfill_stops_when_quota_is_near_limit(scheduler):
    scheduler.dao.get_current_usage.return_value = 1900

    await scheduler.run_backfill_job()

    scheduler.dao.get_items_for_backfill.assert_not_called()


@pytest.mark.asyncio
async def test_backfill_records_failure(monkeypatch, scheduler):
    """失敗したアイテムが is_estimated=3 (失敗) として記録されること。"""
    monkeypatch.setattr("shelf_aware.scheduler.asyncio.sleep", AsyncMock())
    scheduler.dao.get_items_for_backfill.return_value = [{"id": "豆板醤", "is_estimated": 0, "attempt_count": 0}]
    scheduler.estimator.estimate_expiration = AsyncMock(
        return_value=EstimationOutcome(EstimationResult.ERROR, reason="no_days_extracted")
    )

    await scheduler.run_backfill_job()

    scheduler.dao.mark_estimation_failed.assert_called_once_with("豆板醤", "no_days_extracted")


@pytest.mark.asyncio
async def test_backfill_uses_max_attempts(monkeypatch, scheduler):
    monkeypatch.setattr("shelf_aware.scheduler.asyncio.sleep", AsyncMock())

    await scheduler.run_backfill_job()

    scheduler.dao.get_items_for_backfill.assert_called_once_with(
        limit=settings.BACKFILL_BATCH_SIZE, max_attempts=settings.MAX_ESTIMATION_ATTEMPTS
    )
