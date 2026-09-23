# src/shelf_aware/scheduler.py
"""
日次バックフィル（未処理・失敗アイテムの再推定）。

Raspberry Pi 5 前提の注意点:
- DAO / Estimator は RAGService が持つインスタンスを共有する。
  ここで新規に作ると SentenceTransformer と ChromaDB が二重にロードされ、
  8GBのPiではメモリを大きく圧迫するため。
- 実行時刻は日本時間 (Asia/Tokyo)。コンテナのTZがUTCでも意図した時刻に動くよう、
  CronTrigger にタイムゾーンを明示する。
"""

import asyncio
import logging
from typing import TYPE_CHECKING, Optional
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from shelf_aware.config import settings
from shelf_aware.estimation import apply_estimation_outcome

if TYPE_CHECKING:
    from shelf_aware.database import InventoryDAO
    from shelf_aware.estimation import ExpirationEstimator

logger = logging.getLogger(__name__)


def _resolve_timezone() -> Optional[ZoneInfo]:
    """設定されたタイムゾーンを解決する（tzdataが無い環境でも落とさない）。"""
    try:
        return ZoneInfo(settings.BACKFILL_TIMEZONE)
    except Exception as e:  # noqa: BLE001 - tzdata未インストール時はローカル時刻で動かす
        logger.warning(f"⚠️ Timezone '{settings.BACKFILL_TIMEZONE}' is unavailable ({e}). Using local time.")
        return None


class BackfillScheduler:
    def __init__(self, dao: "InventoryDAO", estimator: "ExpirationEstimator"):
        # 呼び出し側（main.py の lifespan）が RAGService のインスタンスを渡す
        self.dao = dao
        self.estimator = estimator
        self.timezone = _resolve_timezone()
        # timezone=None を明示的に渡すとAPScheduler側で扱いが変わるため、解決できた場合のみ渡す
        self.scheduler = AsyncIOScheduler(timezone=self.timezone) if self.timezone else AsyncIOScheduler()

        # 安全装置: 月間制限の9割を超えたらバックフィルは停止する
        self.SAFETY_QUOTA_LIMIT = 1800
        # 1日あたりの処理件数（API節約のため）
        self.DAILY_BATCH_SIZE = settings.BACKFILL_BATCH_SIZE
        # 同じアイテムを失敗として記録する上限（無限リトライ防止）
        self.MAX_ATTEMPTS = settings.MAX_ESTIMATION_ATTEMPTS

    def start(self):
        """スケジューラーを開始 (既定: 毎日 3:00 JST)"""
        trigger = CronTrigger(
            hour=settings.BACKFILL_HOUR,
            minute=0,
            timezone=self.timezone,
        )
        self.scheduler.add_job(
            self.run_backfill_job,
            trigger,
            id="daily_backfill",
            replace_existing=True,
            # Piが一時停止・再起動していても、1時間以内の遅延なら実行する
            misfire_grace_time=3600,
            coalesce=True,
            max_instances=1,
        )
        self.scheduler.start()
        tz_label = self.timezone or "local time"
        logger.info(f"🕒 Backfill Scheduler started (daily at {settings.BACKFILL_HOUR}:00 {tz_label})")

    def shutdown(self):
        """アプリ終了時にスケジューラーを止める"""
        try:
            if self.scheduler.running:
                self.scheduler.shutdown(wait=False)
                logger.info("🕒 Backfill Scheduler stopped.")
        except Exception as e:  # noqa: BLE001 - 終了処理でアプリを落とさない
            logger.warning(f"Backfill Scheduler shutdown failed: {e}")

    async def run_backfill_job(self):
        """
        バックフィル実行本体。
        未処理(0)と失敗(3)のアイテムに対して推定を試み、結果に応じてステータスを確定させる。
        """
        logger.info("🧹 Starting Daily Backfill Job...")

        # 1. Quota Check (安全装置)
        current_usage = self.dao.get_current_usage("brave_search")
        if current_usage > self.SAFETY_QUOTA_LIMIT:
            logger.warning(f"⚠️ Monthly quota near limit ({current_usage}/2000). Skipping backfill.")
            return

        # 2. Fetch Candidates (is_estimated=0 または 3、試行回数が上限未満)
        targets = self.dao.get_items_for_backfill(limit=self.DAILY_BATCH_SIZE, max_attempts=self.MAX_ATTEMPTS)
        if not targets:
            logger.info("✅ No items need backfilling.")
            return

        target_ids = [(t["id"], t.get("is_estimated"), t.get("attempt_count", 0)) for t in targets]
        logger.info(f"📋 Backfill Targets: {target_ids}")

        # 3. Processing Loop
        for item in targets:
            item_name = item["id"]
            try:
                # Estimatorに self.dao を渡して実行 (Connection共有)
                outcome = await self.estimator.estimate_expiration(item_name, self.dao)

                # --- 結果の反映 (dispatch経路と共通の処理) ---
                apply_estimation_outcome(self.dao, item_name, outcome)

                # APIへの配慮（インターバル）
                await asyncio.sleep(2)

            except Exception as e:
                logger.error(f"❌ Backfill Error for {item_name}: {e}")

        logger.info("💤 Backfill Job Completed.")
