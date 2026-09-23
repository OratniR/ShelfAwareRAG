# scripts/manual_backfill_heavy.py
import asyncio
import logging
import logging.config
import os
import sys

# プロジェクトルート(src)へのパスを通す
sys.path.append(os.path.join(os.path.dirname(__file__), "../src"))


from shelf_aware.config import settings
from shelf_aware.database import InventoryDAO
from shelf_aware.estimation import EstimationResult, ExpirationEstimator, apply_estimation_outcome
from shelf_aware.logging_config import LOGGING_CONFIG

# Load Logging Config
logging.config.dictConfig(LOGGING_CONFIG)
logger = logging.getLogger(__name__)


async def main():
    TARGET_COUNT = 40  # 今回の目標件数

    logger.info(f"🚀 Starting Manual Backfill (Target: {TARGET_COUNT} items)...")

    dao = InventoryDAO()
    estimator = ExpirationEstimator()

    # 1. 処理対象を取得 (未処理 と 失敗 のうち、試行回数が上限未満のもの)
    targets = dao.get_items_for_backfill(limit=TARGET_COUNT, max_attempts=settings.MAX_ESTIMATION_ATTEMPTS)

    if not targets:
        logger.info("✅ No items need backfilling. (All caught up!)")
        return

    logger.info(f"📋 Found {len(targets)} items to process.")

    # 2. ループ処理
    success_count = 0
    skipped_count = 0
    non_food_count = 0
    error_count = 0

    for i, item in enumerate(targets, 1):
        item_name = item["id"]
        logger.info(f"[{i}/{len(targets)}] Processing: {item_name} ...")

        try:
            # 推定実行 (DAOを渡す)
            outcome = await estimator.estimate_expiration(item_name, dao)

            # 結果の反映 (dispatch経路と共通の処理)
            status = apply_estimation_outcome(dao, item_name, outcome)

            if status == EstimationResult.SUCCESS:
                logger.info(f"  ✅ Updated: {outcome.data['expiry_date']} ({outcome.data['reason']})")
                success_count += 1

            elif status == EstimationResult.NON_FOOD:
                logger.info("  🚫 Marked as Non-Food")
                non_food_count += 1

            elif status == EstimationResult.SKIPPED:
                logger.warning(f"  ⏭️ Skipped (Rate Limit or No Data): {outcome.reason}")
                skipped_count += 1

            else:
                logger.error(f"  ⚠️ Failed: {outcome.reason}")
                error_count += 1

            # APIレート制限（Braveは1秒1回）への配慮 + 少し余裕を持つ
            logger.info("  💤 Cooling down for 10 seconds...")
            await asyncio.sleep(10.0)

        except Exception as e:
            logger.error(f"  ❌ Error processing {item_name}: {e}")

    # 3. 完了報告
    logger.info("-" * 40)
    logger.info("🏁 Batch Completed.")
    logger.info(f"   Success  : {success_count}")
    logger.info(f"   Non-Food : {non_food_count}")
    logger.info(f"   Skipped  : {skipped_count}")
    logger.info(f"   Failed   : {error_count}")
    logger.info("-" * 40)


if __name__ == "__main__":
    asyncio.run(main())
