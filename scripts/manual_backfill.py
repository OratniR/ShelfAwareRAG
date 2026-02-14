# scripts/manual_backfill_heavy.py
import asyncio
import logging
import logging.config
import os
import sys

# プロジェクトルート(src)へのパスを通す
sys.path.append(os.path.join(os.path.dirname(__file__), "../src"))


from shelf_aware.database import InventoryDAO
from shelf_aware.estimation import EstimationResult, ExpirationEstimator
from shelf_aware.logging_config import LOGGING_CONFIG

# Load Logging Config
logging.config.dictConfig(LOGGING_CONFIG)
logger = logging.getLogger(__name__)


async def main():
    TARGET_COUNT = 40  # 今回の目標件数

    logger.info(f"🚀 Starting Manual Backfill (Target: {TARGET_COUNT} items)...")

    dao = InventoryDAO()
    estimator = ExpirationEstimator()

    # 1. 処理対象を取得
    targets = dao.get_items_for_backfill(limit=TARGET_COUNT)

    if not targets:
        logger.info("✅ No items need backfilling. (All caught up!)")
        return

    logger.info(f"📋 Found {len(targets)} items to process.")

    # 2. ループ処理
    success_count = 0
    skipped_count = 0
    non_food_count = 0

    for i, item in enumerate(targets, 1):
        item_name = item["id"]
        logger.info(f"[{i}/{len(targets)}] Processing: {item_name} ...")

        try:
            # 推定実行 (DAOを渡す)
            result_packet = await estimator.estimate_expiration(item_name, dao)

            status = result_packet["status"]
            data = result_packet["data"]

            if status == EstimationResult.SUCCESS and data:
                dao.update_expiry(item_name, data["expiry_date"])
                logger.info(f"  ✅ Updated: {data['expiry_date']} ({data['reason']})")
                success_count += 1

            elif status == EstimationResult.NON_FOOD:
                dao.mark_as_non_food(item_name)
                logger.info("  🚫 Marked as Non-Food")
                non_food_count += 1

            elif status == EstimationResult.SKIPPED:
                logger.warning("  ⏭️ Skipped (Rate Limit or No Data)")
                skipped_count += 1

            else:
                logger.error(f"  ❓ Unknown Status: {status}")

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
    logger.info("-" * 40)


if __name__ == "__main__":
    asyncio.run(main())
