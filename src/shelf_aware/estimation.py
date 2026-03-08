# src/shelf_aware/estimation.py
import asyncio
import datetime as dt
import json
import logging
import math
import re
import statistics
import time
from enum import Enum
from typing import TYPE_CHECKING, Any, Dict, List

import httpx
from langfuse import observe

from shelf_aware.config import settings
from shelf_aware.constants import EXCLUDED_DOMAINS
from shelf_aware.prompts import EXPIRATION_ESTIMATION_PROMPT, FOOD_CLASSIFICATION_PROMPT

if TYPE_CHECKING:
    from shelf_aware.database import InventoryDAO

logger = logging.getLogger(__name__)


class EstimationResult(Enum):
    SUCCESS = "success"  # 推定成功
    NON_FOOD = "non_food"  # 食品ではない
    SKIPPED = "skipped"  # クォータ制限などでスキップ
    ERROR = "error"  # エラー


class ExpirationEstimator:
    """
    賞味期限推定の責任を持つクラス。
    Brave Searchによる検索と、LLMによる情報抽出、統計的な期間算出を行う。
    """

    def __init__(self):
        self.brave_api_key = settings.BRAVE_API_KEY
        if not self.brave_api_key:
            logger.warning("⚠️ BRAVE_API_KEY is missing. Estimation will be skipped.")

        base_url = settings.LLM_API_BASE
        if base_url.endswith("/"):
            base_url = base_url[:-1]

        self.llm_api_url = f"{base_url}/chat/completions"
        self.llm_api_key = settings.LLM_API_KEY

        self._last_call_time = 0
        self._lock = asyncio.Lock()
        self.MONTHLY_LIMIT = 2000

    @observe()
    async def estimate_expiration(self, item_name: str, dao: "InventoryDAO") -> Dict[str, Any]:
        # 1. 名前クリーニング ("オリーブオイルのストック" -> "オリーブオイル")
        clean_name = self._clean_item_name(item_name)
        if clean_name != item_name:
            logger.info(f"🧹 Name Cleaned: '{item_name}' -> '{clean_name}'")

        if not self.brave_api_key:
            return {"status": EstimationResult.SKIPPED, "data": None}

        # --- Phase 1: 事前判定 (Cost: Free) ---
        classification = await self._classify_item_type(clean_name)
        if not classification.get("is_food"):
            logger.info(f"🍎 -> 🚫 Phase 1: '{clean_name}' classified as NON-FOOD.")
            return {"status": EstimationResult.NON_FOOD, "data": None}

        logger.info(f"🍎 -> 🔍 Phase 1: '{item_name}' seems to be food. Searching...")

        # --- Phase 2: 検索と詳細推定 (Cost: 1 API Call) ---
        query = f"{clean_name} 賞味期限 日持ち 未開封"
        context = await self._search_brave(query, dao)

        if not context:
            # 検索した結果何も出なかった、またはレート制限で弾かれた
            return {"status": EstimationResult.SKIPPED, "data": None}

        extracted_data = await self._call_llm(clean_name, context)

        # Phase 1ですり抜けたが、Phase 2でやはり食品ではないと判定された場合
        if not extracted_data.get("is_food"):
            return {"status": EstimationResult.NON_FOOD, "data": None}

        days_list = extracted_data.get("extracted_days", [])
        estimated_days = self._calculate_geometric_mean(days_list)

        if estimated_days:
            # 【修正】dt.datetime.now() と dt.timedelta を使用
            expiry_date = (dt.datetime.now() + dt.timedelta(days=estimated_days)).date().isoformat()
            return {
                "status": EstimationResult.SUCCESS,
                "data": {
                    "expiry_date": expiry_date,
                    "days_offset": estimated_days,
                    "reason": extracted_data.get("reason", "検索結果より推定"),
                },
            }

        # 日数が算出できなかった
        return {"status": EstimationResult.SKIPPED, "data": None}

    def _clean_item_name(self, name: str) -> str:
        """
        アイテム名からノイズを除去する。
        例: "オリーブオイルのストック" -> "オリーブオイル"
        """
        # "のストック", " ストック", "ストック", "の在庫" などを削除
        cleaned = re.sub(r"(\s|の)?(ストック|在庫)$", "", name)
        return cleaned.strip()

    @observe(as_type="generation")
    async def _classify_item_type(self, item_name: str) -> Dict[str, Any]:
        """[Phase 1] 食品判定 (タイムアウトを60秒に延長)"""
        prompt = FOOD_CLASSIFICATION_PROMPT.format(item_name=item_name)

        payload = {
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.0,
            "max_tokens": 20,
            # "response_format": {"type": "json_object"},
            "model": settings.LLM_MODEL,
        }

        headers = {
            "Authorization": f"Bearer {self.llm_api_key}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient() as client:
            try:
                # Raspberry Pi用にタイムアウトを大幅延長 (10s -> 60s)
                resp = await client.post(self.llm_api_url, json=payload, headers=headers, timeout=120.0)
                print(f"HTTP_STATUS: {resp.status_code}", flush=True)
                content = resp.json()["choices"][0]["message"]["content"].strip().lower()
                print(f"RAW: {repr(content)}", flush=True)

                is_food = "non-food" not in content
                return {"is_food": is_food}

            except Exception as e:
                # エラー詳細をログに出す
                logger.warning(f"Classification Warning (Defaulting to Food): {e}")
                return {"is_food": True}

    @observe()
    async def _search_brave(self, query: str, dao: "InventoryDAO") -> str:
        """[Private] Brave Search APIを叩き、Contextとなるテキストを生成する"""
        if not self.brave_api_key:
            return ""

        async with self._lock:
            # 1. 秒間1回制限 (Rate Limiting)
            now = time.time()
            elapsed = now - self._last_call_time
            if elapsed < 1.1:
                await asyncio.sleep(1.1 - elapsed)

            can_execute = dao.check_and_increment_usage("brave_search", self.MONTHLY_LIMIT)
            if not can_execute:
                logger.error("⛔ Monthly limit reached.")
                return ""

            # 実行時刻を更新
            self._last_call_time = time.time()
            current = dao.get_current_usage("brave_search")
            logger.info(f"Brave API Call: {current}/{self.MONTHLY_LIMIT}")

        url = "https://api.search.brave.com/res/v1/web/search"
        headers = {
            "Accept": "application/json",
            "X-Subscription-Token": self.brave_api_key,
        }
        exclusion_query = " ".join([f"-site:{domain}" for domain in EXCLUDED_DOMAINS])
        final_query = f"{query} {exclusion_query}"
        params = {"q": final_query, "count": 2, "country": "JP", "search_lang": "jp"}

        async with httpx.AsyncClient() as client:
            try:
                resp = await client.get(url, headers=headers, params=params, timeout=60.0)
                if resp.status_code == 429:
                    return ""
                resp.raise_for_status()
                data = resp.json()
                snippets = []
                for r in data.get("web", {}).get("results", []):
                    title = r.get("title", "")
                    desc = r.get("description", "")
                    # 【修正】文字数を 80文字 に制限 (Input Token削減が一番効く)
                    snippets.append(f"- {title}: {desc[:80]}...")
                return "\n".join(snippets)
            except Exception as e:
                logger.error(f"Brave Search Error: {e}")
                return ""

    @observe(as_type="generation")
    async def _call_llm(self, item_name: str, context: str) -> Dict[str, Any]:
        """[Private] LLMにContextを渡し、JSON形式で日数リスト等を抽出させる"""
        logger.info(f"context:\n{context}")
        safe_context = context[:800]
        logger.info(f"safe_context:\n{safe_context}")
        prompt = EXPIRATION_ESTIMATION_PROMPT.format(item_name=item_name, context_text=safe_context)

        payload = {
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.0,
            "max_tokens": 30,
            # "response_format": {"type": "json_object"},
            "model": settings.LLM_MODEL,
        }

        headers = {
            "Authorization": f"Bearer {self.llm_api_key}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient() as client:
            try:
                resp = await client.post(self.llm_api_url, json=payload, headers=headers, timeout=120.0)
                resp.raise_for_status()
                content = resp.json()["choices"][0]["message"]["content"]
                content = content.replace("```json", "").replace("```", "").strip()
                if re.search(r"false", content, re.IGNORECASE):
                    return {"is_food": False}
                elif re.search(r"true", content, re.IGNORECASE):
                    return {"is_food": True}
                else:
                    return json.loads(content)
            except Exception as e:
                logger.error(f"LLM Extraction Error: {e}")
                return {"is_food": False}

    def _calculate_geometric_mean(self, days_list: List[float]) -> int:
        valid_days = [d for d in days_list if d > 0]
        if not valid_days:
            return None
        if len(valid_days) == 1:
            return int(valid_days[0])
        try:
            # Formula: exp( sum(log(x)) / n )
            log_sum = sum(math.log(x) for x in valid_days)
            return int(math.exp(log_sum / len(valid_days)))
        except Exception as e:
            logger.warning(f"Geometric mean calc failed: {e}, using median.")
            return int(statistics.median(valid_days))
