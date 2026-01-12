# tests/integration/test_integration.py
import pytest
import os
import httpx
from dotenv import load_dotenv
from shelf_aware.estimation import ExpirationEstimator
load_dotenv()
@pytest.fixture
def estimator():
    return ExpirationEstimator()

@pytest.mark.asyncio
@pytest.mark.integration
async def test_real_brave_search_connection(estimator):
    if not os.getenv("BRAVE_API_KEY"):
        pytest.skip("BRAVE_API_KEY is not set")

    query = "納豆 賞味期限 日持ち"
    result_text = await estimator._search_brave(query)
    assert result_text is not None
    assert len(result_text) > 0

@pytest.mark.asyncio
@pytest.mark.integration
async def test_real_llm_json_format(estimator):
    # LLMサーバーのURL確認
    llm_url = os.getenv("LLM_API_URL", "http://localhost:8000/v1/chat/completions")
    
    async with httpx.AsyncClient() as client:
        try:
            await client.get(llm_url.replace("/chat/completions", "/models"), timeout=3.0)
        except Exception:
            pytest.skip(f"LLM Server is not reachable at {llm_url}")

    # テスト
    item_name = "未開封の牛乳"
    dummy_context = "牛乳は冷蔵で1週間ほど持ちます。"
    result_json = await estimator._call_llm(item_name, dummy_context)

    assert isinstance(result_json, dict)
    assert "is_food" in result_json