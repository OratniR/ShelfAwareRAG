# tests/integration/test_integration.py
import os
from unittest.mock import MagicMock

import pytest
from dotenv import load_dotenv

from shelf_aware.config import settings
from shelf_aware.estimation import ExpirationEstimator
from tests._llm import llm_server_available

load_dotenv()


@pytest.fixture
def estimator():
    return ExpirationEstimator()


@pytest.fixture
def mock_dao():
    """Mock DAO for tests that need it but don't test DB logic."""
    dao = MagicMock()
    dao.check_and_increment_usage.return_value = True
    dao.get_current_usage.return_value = 0
    return dao


@pytest.mark.asyncio
@pytest.mark.integration
async def test_real_brave_search_connection(estimator, mock_dao):
    if not os.getenv("BRAVE_API_KEY"):
        pytest.skip("BRAVE_API_KEY is not set")

    query = "納豆 賞味期限 日持ち"
    result_text = await estimator._search_brave(query, mock_dao)
    assert result_text is not None
    assert len(result_text) > 0


@pytest.mark.asyncio
@pytest.mark.integration
async def test_real_llm_json_format(estimator):
    """実LLMサーバーが応答する場合のみ、JSON抽出が機能することを確認する。"""
    if not await llm_server_available():
        pytest.skip(f"LLM Server is not reachable at {settings.LLM_API_BASE}")

    item_name = "未開封の牛乳"
    dummy_context = "牛乳は冷蔵で1週間ほど持ちます。"
    result_json = await estimator._call_llm(item_name, dummy_context)

    assert isinstance(result_json, dict)
    assert "is_food" in result_json
    # 解釈不能な出力は例外になるため、ここまで来たら日数が取れているはず
    assert result_json.get("extracted_days"), f"日数が抽出できていない: {result_json}"
