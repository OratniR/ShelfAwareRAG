# src/shelf_aware/scheduler.py
import logging
import asyncio
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

# Local modules
from shelf_aware.database import InventoryDAO
from shelf_aware.estimation import ExpirationEstimator, EstimationResult

logger = logging.getLogger(__name__)

class BackfillScheduler:
    def __init__(self):
        self.scheduler = AsyncIOScheduler()
        # 共通のDAOを使用する（競合回避のため、Estimatorにはこれを渡す）
        self.dao = InventoryDAO()
        self.estimator = ExpirationEstimator()
        
        # 安全装置: 月間制限の9割を超えたらバックフィルは停止する
        self.SAFETY_QUOTA_LIMIT = 1800 
        # 1日あたりの処理件数（API節約のため）
        self.DAILY_BATCH_SIZE = 5

    def start(self):
        """スケジューラーを開始 (毎日 AM 3:00)"""
        trigger = CronTrigger(hour=3, minute=0)
        self.scheduler.add_job(self.run_backfill_job, trigger)
        self.scheduler.start()
        logger.info("🕒 Backfill Scheduler started (Runs daily at 3:00 AM)")

    async def run_backfill_job(self):
        """
        バックフィル実行本体。
        未処理アイテムに対して推定を試み、結果に応じてステータスを確定させる。
        """
        logger.info("🧹 Starting Daily Backfill Job...")
        
        # 1. Quota Check (安全装置)
        current_usage = self.dao.get_current_usage("brave_search")
        if current_usage > self.SAFETY_QUOTA_LIMIT:
            logger.warning(f"⚠️ Monthly quota near limit ({current_usage}/2000). Skipping backfill.")
            return

        # 2. Fetch Candidates (is_estimated=0 のもの)
        targets = self.dao.get_items_for_backfill(limit=self.DAILY_BATCH_SIZE)
        if not targets:
            logger.info("✅ No items need backfilling.")
            return

        target_ids = [t['id'] for t in targets]
        logger.info(f"📋 Backfill Targets: {target_ids}")

        # 3. Processing Loop
        for item in targets:
            item_name = item['id']
            try:
                # Estimatorに self.dao を渡して実行 (Connection共有)
                result_packet = await self.estimator.estimate_expiration(item_name, self.dao)
                
                status = result_packet["status"]
                data = result_packet["data"]

                # --- Branching Logic ---
                if status == EstimationResult.SUCCESS and data:
                    # 食品 -> 日付更新 (is_estimated=1)
                    self.dao.update_expiry(item_name, data["expiry_date"])
                    logger.info(f"✅ Backfill Success: {item_name} -> {data['expiry_date']}")

                elif status == EstimationResult.NON_FOOD:
                    # 食品ではない -> 対象外マーク (is_estimated=2)
                    # これにより、明日のジョブでは取得されなくなる（重要）
                    self.dao.mark_as_non_food(item_name)
                    logger.info(f"🚫 Backfill Ignored (Non-Food): {item_name}")

                elif status == EstimationResult.SKIPPED:
                    # レート制限や検索ヒットなし -> ログのみ (次回リトライ)
                    logger.info(f"⏭️ Backfill Skipped: {item_name}")

                else:
                    logger.warning(f"❓ Unknown status for {item_name}: {status}")
                
                # APIへの配慮（インターバル）
                await asyncio.sleep(2)

            except Exception as e:
                logger.error(f"❌ Backfill Error for {item_name}: {e}")

        logger.info("💤 Backfill Job Completed.")