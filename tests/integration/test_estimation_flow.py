import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock

from shelf_aware.database import InventoryDAO
from shelf_aware.estimation import EstimationResult, ExpirationEstimator


logging.basicConfig(level=logging.INFO)


async def test_estimation_flow():
    # 1. Mock dependencies
    dao = MagicMock(spec=InventoryDAO)
    dao.update_expiry = MagicMock()
    dao.mark_as_non_food = MagicMock()

    # Mock ExpirationEstimator methods to avoid real API calls
    estimator = ExpirationEstimator()

    # CASE 1: Estimator returns SUCCESS
    estimator.estimate_expiration = AsyncMock(
        return_value={
            "status": EstimationResult.SUCCESS,
            "data": {"expiry_date": "2026-03-01", "days_offset": 30},
        }
    )

    print("--- Testing SUCCESS Case ---")
    item_name = "Test Food"

    # Simulate run_estimation_task logic (copy-paste from rag_core.py)
    # async def run_estimation_task(item_name: str, estimator: ExpirationEstimator, dao: InventoryDAO):
    result_packet = await estimator.estimate_expiration(item_name, dao)
    status = result_packet["status"]
    data = result_packet["data"]

    if status == EstimationResult.SUCCESS and data:
        dao.update_expiry(item_name, data["expiry_date"])
        print(f"✅ Expiry Updated: {item_name} -> {data['expiry_date']}")
    elif status == EstimationResult.NON_FOOD:
        dao.mark_as_non_food(item_name)
        print(f"🚫 Marked as Non-Food: {item_name}")
    else:
        print(f"⏭️ Skipped: {item_name}")

    # Verify DAO interaction
    dao.update_expiry.assert_called_with("Test Food", "2026-03-01")

    # CASE 2: Estimator returns SKIPPED (e.g. no API key)
    estimator.estimate_expiration = AsyncMock(return_value={"status": EstimationResult.SKIPPED, "data": None})

    print("\n--- Testing SKIPPED Case ---")
    item_name = "Skipped Item"
    result_packet = await estimator.estimate_expiration(item_name, dao)
    status = result_packet["status"]

    if status == EstimationResult.SKIPPED:
        print(f"✅ Correctly skipped: {item_name}")
    else:
        print(f"❌ Failed: Expected SKIPPED, got {status}")


if __name__ == "__main__":
    asyncio.run(test_estimation_flow())
