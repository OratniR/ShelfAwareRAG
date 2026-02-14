# tests/unit/test_estimation.py
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shelf_aware.estimation import ExpirationEstimator


@pytest.fixture
def estimator():
    return ExpirationEstimator()


# --- ロジックのテスト (Mock不要) ---
def test_calculate_geometric_mean_normal(estimator):
    assert estimator._calculate_geometric_mean([3, 12]) == 6
    assert estimator._calculate_geometric_mean([2, 10]) == 4


def test_calculate_geometric_mean_single(estimator):
    assert estimator._calculate_geometric_mean([30]) == 30


def test_calculate_geometric_mean_edge_cases(estimator):
    assert estimator._calculate_geometric_mean([]) is None
    assert estimator._calculate_geometric_mean([0, -5]) is None


# --- LLMパースのテスト (Mock使用) ---
@pytest.mark.asyncio
async def test_call_llm_clean_json(estimator):
    mock_json = {"choices": [{"message": {"content": '{"is_food": true, "extracted_days": [7], "reason": "ok"}'}}]}

    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = mock_json

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_resp
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("shelf_aware.estimation.httpx.AsyncClient", return_value=mock_client):
        result = await estimator._call_llm("納豆", "context")
        assert result["is_food"] is True
        assert result["extracted_days"] == [7]

