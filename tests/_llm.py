"""統合テスト用のヘルパー（実LLMサーバーの疎通確認）。"""

import httpx

from shelf_aware.config import settings


async def llm_server_available() -> bool:
    """推定に使うLLMサーバー (llama-server) が応答するかどうか。"""
    url = f"{settings.LLM_API_BASE.rstrip('/')}/models"
    async with httpx.AsyncClient() as client:
        try:
            await client.get(url, timeout=3.0)
            return True
        except Exception:
            return False
