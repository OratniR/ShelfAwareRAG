# src/shelf_aware/estimation.py
"""
賞味期限推定パイプライン。

設計方針:
- 「実行しなかった (SKIPPED)」と「実行して失敗した (ERROR)」を厳密に区別する。
  SKIPPED は DB を一切変更しない（クォータ切れ等。次回のバックフィルで再挑戦）。
  ERROR は is_estimated=3 (失敗) として記録し、理由を last_error に残す。
- 小型LLM(Qwen/Gemma 2B級)の出力は崩れる前提で扱う。
  「完全JSON → フェンス除去 → 部分抽出 → 正規表現サルベージ」の多段で解釈し、
  それでも駄目なら LLMExtractionError を送出する（黙って握りつぶさない）。
- 失敗は必ずログと Langfuse (level=ERROR) に残す。
"""

import asyncio
import datetime as dt
import json
import logging
import math
import re
import statistics
import time
import unicodedata
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence, Tuple

import httpx
from langfuse import get_client, observe

from shelf_aware.config import settings
from shelf_aware.constants import EXCLUDED_DOMAINS
from shelf_aware.prompts import (
    EXPIRATION_ESTIMATION_PROMPT,
    EXPIRATION_ESTIMATION_RETRY_PROMPT,
    FOOD_CLASSIFICATION_PROMPT,
)

if TYPE_CHECKING:
    from shelf_aware.database import InventoryDAO

logger = logging.getLogger(__name__)

# --- パイプラインの調整値 ---
# Raspberry Pi 5 はCPUのみで1回の生成に20〜40秒かかるため、タイムアウトは長めに取る。
CLASSIFY_TIMEOUT_SECONDS = 120.0
LLM_TIMEOUT_SECONDS = 180.0
EXTRACTION_MAX_TOKENS = 400
SEARCH_RESULT_COUNT = 5
# LLMに渡す検索コンテキストの上限文字数（llama-server のコンテキスト窓と相談）
CONTEXT_CHAR_BUDGET = 1200
# 常識外れの推定値（10年超）は捨てる
MAX_REASONABLE_DAYS = 3650.0
# これ以下の裸の数値は「2年」の "2" のように単位を落としている疑いが強いとみなす
# （この値を超える裸の数値は、日数として妥当なので再試行しない）
UNIT_AMBIGUOUS_MAX_DAYS = 31.0


class EstimationResult(str, Enum):
    SUCCESS = "success"  # 推定成功
    NON_FOOD = "non_food"  # 食品ではない
    SKIPPED = "skipped"  # クォータ制限などで「実行しなかった」（DBは変更しない）
    ERROR = "error"  # 実行したが失敗した（DBに失敗として記録する）


class EstimationSkipped(Exception):
    """APIクォータ切れ等で「実行しなかった」ことを表す。DBは変更しない。"""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class LLMExtractionError(RuntimeError):
    """LLMの出力から必要な情報を取り出せなかった（一過性の可能性がある）。"""

    def __init__(self, reason: str, raw: str = "", finish_reason: Optional[str] = None):
        super().__init__(reason)
        self.reason = reason
        self.raw = raw
        self.finish_reason = finish_reason


@dataclass(frozen=True)
class EstimationOutcome:
    """推定処理の結果。呼び出し側は apply_estimation_outcome() でDBへ反映する。"""

    status: EstimationResult
    data: Optional[Dict[str, Any]] = None
    reason: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return {"status": self.status.value, "data": self.data, "reason": self.reason}


def update_langfuse(kind: str, **fields: Any) -> None:
    """
    現在のLangfuse observationを更新する。

    LangfuseのSDK差異・未設定・ネットワーク断で推定本体を落とさないよう、例外は握る。
    kind: "generation" | "span"
    """
    fields = {key: value for key, value in fields.items() if value is not None}
    if not fields:
        return
    try:
        client = get_client()
        updater = client.update_current_generation if kind == "generation" else client.update_current_span
        updater(**fields)
    except Exception as e:  # noqa: BLE001 - 可観測性の失敗でパイプラインを止めない
        logger.debug(f"Langfuse update skipped ({kind}): {e}")


# ---------------------------------------------------------------------------
# 純粋関数（LLM出力の解釈・検索結果の整形）: pytest から直接テストできるよう分離
# ---------------------------------------------------------------------------

_HTML_TAG_RE = re.compile(r"<[^>]{1,40}>")
_DATE_LITERAL_RE = re.compile(r"\d{2,4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日|\d{1,2}\s*月\s*\d{1,2}\s*日")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。．！？!?])")
_EXPIRY_KEYWORD_RE = re.compile(r"期限|賞味|消費|日持|保存|開封|ヶ月|ヵ月|か月|カ月|週間|年間|日間|年|週|日")
_FENCE_RE = re.compile(r"```(?:json)?", re.IGNORECASE)
_HALF_YEAR_RE = re.compile(r"半年")
# (正規表現, 1単位あたりの日数) の順に評価する。年→月→週→日の順は入れ替えないこと。
_DURATION_UNITS: Tuple[Tuple[re.Pattern, float], ...] = (
    (re.compile(r"(\d+(?:\.\d+)?)\s*(?:年間|年)"), 365.0),
    (re.compile(r"(\d+(?:\.\d+)?)\s*(?:ヶ月|ヵ月|ケ月|か月|カ月|箇月)"), 30.0),
    (re.compile(r"(\d+(?:\.\d+)?)\s*(?:週間|週)"), 7.0),
    (re.compile(r"(\d+(?:\.\d+)?)\s*(?:日間|日)"), 1.0),
)
_SALVAGE_IS_FOOD_RE = re.compile(r'"is[_\- ]?food"\s*[:：]\s*"?(true|false|yes|no|はい|いいえ)"?', re.IGNORECASE)
_SALVAGE_PERIODS_RE = re.compile(r'"(?:periods|extracted[_\- ]?periods|durations)"\s*[:：]\s*\[([^\]]*)', re.DOTALL)
_SALVAGE_DAYS_RE = re.compile(r'"extracted[_\- ]?days?"\s*[:：]\s*\[([^\]]*)', re.DOTALL)
_SALVAGE_REASON_RE = re.compile(r'"reason"\s*[:：]\s*"([^"]{0,120})')
_TRUTHY_WORDS = {"true", "yes", "はい"}
_BARE_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
# LLMが期間を入れてくる可能性のあるキー（表記そのままの文字列を期待する）
_PERIOD_KEYS = ("periods", "extracted_periods", "expiry_periods", "durations")
# 「2年」の "2" のように単位を落とした数値を解釈し直すための根拠として読むキー
_EVIDENCE_KEYS = ("reason", "evidence", "note", "summary")


def _clean_ws(text: Any) -> str:
    """HTMLタグ・改行・連続空白を除去して1行にまとめる（Braveのdescriptionはタグを含む）。"""
    if text is None:
        return ""
    return re.sub(r"\s+", " ", _HTML_TAG_RE.sub("", str(text))).strip()


def _find_duration_expressions(text: str) -> List[Tuple[float, float]]:
    """
    テキスト中の期間表現を (元の数値, 日数) のリストとして返す。

    「2年」-> (2.0, 730.0)。「半年」のように数値を持たない表現は数値 0.0 として返す
    （日数の解釈し直しに使うのは数値付きの表現のみ）。

    「2026年」「3月1日」のような日付リテラルは事前に除去し、誤検出を防ぐ。
    """
    if not text:
        return []
    normalized = unicodedata.normalize("NFKC", text)
    normalized = _DATE_LITERAL_RE.sub(" ", normalized)

    found: List[Tuple[float, float]] = [(0.0, 182.5)] * len(_HALF_YEAR_RE.findall(normalized))
    for pattern, unit_days in _DURATION_UNITS:
        for match in pattern.finditer(normalized):
            value = float(match.group(1))
            # 1000以上は西暦などのノイズとみなす（「2026年」→365日 を防ぐ）
            if value <= 0 or value >= 1000:
                continue
            found.append((value, value * unit_days))
    return found


def _extract_days_from_text(text: str) -> List[float]:
    """
    日本語テキストから「1年」「半年」「2ヶ月」「10日」「1週間」等を日数へ変換して抽出する。
    """
    return [days for _, days in _find_duration_expressions(text)]


def _coerce_days(value: Any) -> List[float]:
    """
    LLMが返した日数表現を数値リストへ正規化する。

    [180, 365] / ["180"] / ["1年", "半年"] / "2ヶ月" のような表記ゆれを吸収する。
    """
    if value is None:
        return []
    items = value if isinstance(value, (list, tuple, set)) else [value]

    days: List[float] = []
    for item in items:
        if isinstance(item, bool):
            continue
        if isinstance(item, (int, float)):
            number = float(item)
        elif isinstance(item, str):
            token = item.strip()
            if _is_bare_number(token):
                number = float(unicodedata.normalize("NFKC", token))
            else:
                days.extend(day for day in _extract_days_from_text(token) if day <= MAX_REASONABLE_DAYS)
                continue
        else:
            continue
        if 0 < number <= MAX_REASONABLE_DAYS:
            days.append(number)
    return days


def _is_bare_number(value: str) -> bool:
    """単位を持たない数値だけの文字列かどうか（例: "2", "365"）。"""
    return bool(_BARE_NUMBER_RE.fullmatch(unicodedata.normalize("NFKC", (value or "").strip())))


def _reinterpret_unitless(numbers: List[float], evidence_text: str) -> Tuple[List[float], List[float]]:
    """
    単位を落とした数値を、応答中の単位付き表現から解釈し直す。

    例: extracted_days=[0, 2], reason="未開封で2年という記述あり"
        -> 2 を「2日」ではなく「2年」= 730日 として扱う。

    単位の根拠が見つからない数値は日数として扱い、unresolved として返す（呼び出し側で警告する）。
    """
    unit_map: Dict[float, float] = {}
    for value, days in _find_duration_expressions(evidence_text):
        if value > 0:
            # _DURATION_UNITS は 年→月→週→日 の順なので、大きい単位が優先される
            unit_map.setdefault(value, days)

    resolved: List[float] = []
    unresolved: List[float] = []
    for number in numbers:
        if number in unit_map:
            resolved.append(unit_map[number])
        else:
            resolved.append(number)
            unresolved.append(number)
    return resolved, unresolved


def _resolve_days(data: Dict[str, Any]) -> Tuple[List[float], List[float]]:
    """
    LLM応答から日数を解決する。

    - 単位付きの表記 ("2年", "半年") はコード側で日数へ換算する（LLMに換算させない）
    - 単位を落とした数値は、同じ数値が「n年」「nヶ月」「n週間」として応答中に現れていれば
      その単位で解釈し直す
    - 根拠が無い裸の数値は日数とみなすが unresolved として返す

    戻り値: (日数リスト, 単位の根拠が無かった数値のリスト)
    """
    raw_values: List[Any] = []
    for key in (*_PERIOD_KEYS, "extracted_days"):
        if key not in data:
            continue
        value = data[key]
        raw_values.extend(list(value) if isinstance(value, (list, tuple, set)) else [value])

    unit_values: List[Any] = []
    numbers: List[float] = []
    evidence: List[str] = []
    for item in raw_values:
        if isinstance(item, bool) or item is None:
            continue
        if isinstance(item, (int, float)):
            numbers.append(float(item))
        elif isinstance(item, str):
            if _is_bare_number(item):
                numbers.append(float(unicodedata.normalize("NFKC", item.strip())))
            else:
                unit_values.append(item)
                evidence.append(item)

    for key in _EVIDENCE_KEYS:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            evidence.append(value)

    resolved, unresolved = _reinterpret_unitless(
        [number for number in numbers if 0 < number <= MAX_REASONABLE_DAYS], " ".join(evidence)
    )
    return _coerce_days(unit_values) + resolved, unresolved


def _first_json_object(text: str) -> Optional[str]:
    match = re.search(r"\{.*?\}", text, re.DOTALL)
    return match.group(0) if match else None


def _outermost_json_object(text: str) -> Optional[str]:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    return text[start : end + 1]


def _parse_llm_json(content: str) -> Optional[Dict[str, Any]]:
    """LLM出力を辞書へ変換する。コードフェンスや前後の説明文があっても拾う。"""
    if not content or not content.strip():
        return None
    text = _FENCE_RE.sub("", content).strip()

    candidates = [text, _first_json_object(text), _outermost_json_object(text)]
    for candidate in candidates:
        if not candidate:
            continue
        try:
            data = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(data, dict):
            return data
    return None


def _salvage_llm_fields(raw: str) -> Optional[Dict[str, Any]]:
    """
    壊れた／途中で切れたJSONから is_food と期間表現だけを正規表現で救出する。

    キー構造が残っている場合のみ救出し、地の文からの推測はしない（誤推定を避けるため）。
    """
    if not raw:
        return None

    is_food: Optional[bool] = None
    match = _SALVAGE_IS_FOOD_RE.search(raw)
    if match:
        is_food = match.group(1).strip().lower() in _TRUTHY_WORDS

    days: List[float] = []
    for pattern in (_SALVAGE_PERIODS_RE, _SALVAGE_DAYS_RE):
        match = pattern.search(raw)
        if match:
            days.extend(_coerce_days([token for token in re.split(r"[,、\s]+", match.group(1)) if token]))

    if is_food is None and not days:
        return None
    if is_food is None:
        # 日数が取れているのに is_food が無い場合は食品とみなす
        is_food = True

    reason_match = _SALVAGE_REASON_RE.search(raw)
    reason = f"{reason_match.group(1)} [salvaged]" if reason_match else "壊れた出力から救出 [salvaged]"
    return {"is_food": is_food, "extracted_days": days, "reason": reason}


def _needs_retry(data: Optional[Dict[str, Any]]) -> bool:
    """
    再試行すべきか。

    - 非食品と明示されていれば再試行しない
    - 日数が取れない場合は再試行する
    - 小さい裸の数値しか無い場合は、単位を落としている疑いが強いので
      表記そのままを求めて再試行する（「2年」の "2" を 2日 と確定してしまう事故を防ぐ）
    """
    if not data:
        return True
    if not data.get("is_food"):
        return False
    days, unresolved = _resolve_days(data)
    if not days:
        return True
    return any(number <= UNIT_AMBIGUOUS_MAX_DAYS for number in unresolved)


def _response_quality(data: Optional[Dict[str, Any]]) -> int:
    """
    応答の品質スコア（大きいほど良い）。再試行結果を採用するかの判断に使う。

    再試行が1回目より悪い場合（非食品に反転した等）に、良い結果を捨てないための仕組み。
    """
    if not data:
        return 0
    if not data.get("is_food"):
        return 1  # 非食品判定も情報としては有効
    days, unresolved = _resolve_days(data)
    if days and not unresolved:
        return 4  # 単位が確定した日数がある = 最良
    if days:
        return 3  # 日数はあるが単位の根拠が弱い
    return 2  # 食品だが日数が無い


def _pick_expiry_sentences(text: str, limit: int = 3) -> str:
    """
    検索結果の説明文から「期限に関係する文」を優先して抜き出す。

    以前は desc[:80] と機械的に切っていたため、賞味期限の数値が入った文が
    捨てられていた（これが推定失敗の一因）。
    """
    cleaned = _clean_ws(text)
    if not cleaned:
        return ""

    sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(cleaned) if s.strip()]
    scored: List[Tuple[int, str]] = []
    for sentence in sentences:
        score = 0
        if _EXPIRY_KEYWORD_RE.search(sentence):
            score += 2
        if re.search(r"\d", sentence):
            score += 1
        if _extract_days_from_text(sentence):
            score += 2
        scored.append((score, sentence))

    picked = [sentence for score, sentence in scored if score > 0][:limit]
    if not picked:
        picked = sentences[:limit]
    return "".join(picked)


def _build_search_context(results: Sequence[Dict[str, Any]], budget: int = CONTEXT_CHAR_BUDGET) -> str:
    """Braveの検索結果をLLM用コンテキスト文字列へ整形する。"""
    lines: List[str] = []
    used = 0
    for result in results:
        title = _clean_ws(result.get("title", ""))
        snippet = (
            _pick_expiry_sentences(result.get("description", "")) or _clean_ws(result.get("description", ""))[:160]
        )
        if not title and not snippet:
            continue
        line = f"- {title}: {snippet}" if title else f"- {snippet}"
        if used + len(line) > budget:
            remaining = budget - used
            if remaining > 40:
                lines.append(line[:remaining])
            break
        lines.append(line)
        used += len(line) + 1
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 推定本体
# ---------------------------------------------------------------------------


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
        # llama-server が response_format 非対応だった場合は自動的に False へ落ちる
        self._json_mode = settings.LLM_JSON_MODE.strip().lower() != "off"

    @observe(capture_input=False, capture_output=False)
    async def estimate_expiration(self, item_name: str, dao: "InventoryDAO") -> EstimationOutcome:
        """賞味期限を推定する。戻り値は EstimationOutcome（DBへの反映は呼び出し側の責務）。"""
        update_langfuse("span", input={"item_name": item_name})

        def done(outcome: EstimationOutcome) -> EstimationOutcome:
            update_langfuse("span", output=outcome.as_dict())
            if outcome.status == EstimationResult.ERROR:
                update_langfuse("span", level="ERROR", status_message=(outcome.reason or "estimation_error")[:300])
            return outcome

        # 1. 名前クリーニング ("オリーブオイルのストック" -> "オリーブオイル")
        clean_name = self._clean_item_name(item_name)
        if clean_name != item_name:
            logger.info(f"🧹 Name Cleaned: '{item_name}' -> '{clean_name}'")

        if not self.brave_api_key:
            return done(EstimationOutcome(EstimationResult.SKIPPED, reason="brave_api_key_missing"))

        # --- Phase 1: 事前判定 (Cost: Free) ---
        try:
            classification = await self._classify_item_type(clean_name)
        except LLMExtractionError as e:
            return done(EstimationOutcome(EstimationResult.ERROR, reason=f"classify_failed: {e.reason}"))

        if not classification.get("is_food"):
            logger.info(f"🍎 -> 🚫 Phase 1: '{clean_name}' classified as NON-FOOD.")
            return done(EstimationOutcome(EstimationResult.NON_FOOD, reason="classified_as_non_food"))

        logger.info(f"🍎 -> 🔍 Phase 1: '{item_name}' seems to be food. Searching...")

        # --- Phase 2: 検索と詳細推定 (Cost: 1 API Call) ---
        query = f"{clean_name} 賞味期限 日持ち 未開封"
        try:
            context = await self._search_brave(query, dao)
        except EstimationSkipped as e:
            # クォータ切れ・レート制限: 実行していないのでDBは変更しない
            return done(EstimationOutcome(EstimationResult.SKIPPED, reason=e.reason))

        if not context:
            return done(EstimationOutcome(EstimationResult.ERROR, reason="search_no_results"))

        try:
            extracted_data = await self._call_llm(clean_name, context)
        except LLMExtractionError as e:
            return done(EstimationOutcome(EstimationResult.ERROR, reason=f"llm_extraction_failed: {e.reason}"))

        # Phase 1ですり抜けたが、Phase 2でやはり食品ではないと判定された場合
        if not extracted_data.get("is_food"):
            return done(EstimationOutcome(EstimationResult.NON_FOOD, reason="llm_says_non_food"))

        days_list, unitless_days = _resolve_days(extracted_data)
        if unitless_days:
            # 「2年」の "2" のような単位落ちの可能性がある。日数として扱うが必ず記録に残す。
            logger.warning(f"⚠️ 単位の根拠が無い数値を日数として扱います: {unitless_days} (item={item_name})")
        estimated_days = self._calculate_geometric_mean(days_list)

        if not estimated_days:
            # 検索は成功したが日数を取り出せなかった = 「失敗」。未処理のまま放置しない。
            return done(EstimationOutcome(EstimationResult.ERROR, reason="no_days_extracted"))

        update_langfuse(
            "span",
            metadata={
                "days_list": days_list,
                "unitless_days": unitless_days,
                "days_source": "assumed_days" if unitless_days else "explicit_unit",
            },
        )

        expiry_date = (dt.datetime.now() + dt.timedelta(days=estimated_days)).date().isoformat()
        return done(
            EstimationOutcome(
                EstimationResult.SUCCESS,
                data={
                    "expiry_date": expiry_date,
                    "days_offset": estimated_days,
                    "reason": extracted_data.get("reason", "検索結果より推定"),
                },
            )
        )

    def _clean_item_name(self, name: str) -> str:
        """
        アイテム名からノイズを除去する。
        例: "オリーブオイルのストック" -> "オリーブオイル"
        """
        # "のストック", " ストック", "ストック", "の在庫" などを削除
        cleaned = re.sub(r"(\s|の)?(ストック|在庫)$", "", name)
        return cleaned.strip()

    @observe(as_type="generation", capture_input=False, capture_output=False)
    async def _classify_item_type(self, item_name: str) -> Dict[str, Any]:
        """[Phase 1] 食品判定。通信・解析の失敗は LLMExtractionError として送出する。"""
        update_langfuse("generation", input={"item_name": item_name})
        prompt = FOOD_CLASSIFICATION_PROMPT.format(item_name=item_name)

        payload = {
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.0,
            "max_tokens": 20,
            "model": settings.LLM_MODEL,
        }
        headers = {
            "Authorization": f"Bearer {self.llm_api_key}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    self.llm_api_url, json=payload, headers=headers, timeout=CLASSIFY_TIMEOUT_SECONDS
                )
                resp.raise_for_status()
                content = (resp.json()["choices"][0]["message"]["content"] or "").strip().lower()
        except Exception as e:
            update_langfuse("generation", level="ERROR", status_message=f"classify_failed: {e}"[:300])
            raise LLMExtractionError(f"classify_failed: {e}") from e

        if not content:
            update_langfuse("generation", level="ERROR", status_message="classify_empty_response")
            raise LLMExtractionError("classify_empty_response")

        is_food = not re.search(r"non[\s\-_]*food", content)
        logger.info(
            f"🍎 Food classification: '{item_name}' -> {'food' if is_food else 'non-food'} (raw={content[:40]!r})"
        )
        update_langfuse(
            "generation",
            output={"is_food": is_food, "raw": content[:100]},
            model=settings.LLM_MODEL,
        )
        return {"is_food": is_food}

    @observe(capture_input=False, capture_output=False)
    async def _search_brave(self, query: str, dao: "InventoryDAO") -> str:
        """
        [Private] Brave Search APIを叩き、LLMに渡すコンテキスト文字列を生成する。

        クォータ切れ・レート制限は「実行しなかった」= EstimationSkipped として送出する。
        """
        update_langfuse("span", input={"query": query})
        if not self.brave_api_key:
            raise EstimationSkipped("brave_api_key_missing")

        async with self._lock:
            # 1. 秒間1回制限 (Rate Limiting)
            now = time.time()
            elapsed = now - self._last_call_time
            if elapsed < 1.1:
                await asyncio.sleep(1.1 - elapsed)

            can_execute = dao.check_and_increment_usage("brave_search", self.MONTHLY_LIMIT)
            if not can_execute:
                logger.error("⛔ Monthly limit reached.")
                raise EstimationSkipped("brave_monthly_limit")

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
        params = {"q": final_query, "count": SEARCH_RESULT_COUNT, "country": "JP", "search_lang": "jp"}

        async with httpx.AsyncClient() as client:
            try:
                resp = await client.get(url, headers=headers, params=params, timeout=60.0)
            except Exception as e:
                logger.error(f"Brave Search Error: {e}")
                return ""

        if resp.status_code == 429:
            # レート制限は「実行しなかった」扱い（次回リトライ）
            raise EstimationSkipped("brave_rate_limited")
        if resp.status_code >= 400:
            logger.error(f"Brave Search HTTP {resp.status_code}: {resp.text[:200]}")
            return ""

        try:
            results = resp.json().get("web", {}).get("results", []) or []
        except Exception as e:
            logger.error(f"Brave Search JSON Error: {e}")
            return ""

        context = _build_search_context(results)
        logger.info(f"🔎 Brave results: {len(results)} hits, context={len(context)} chars")
        update_langfuse("span", output=context)
        return context

    async def _post_llm(self, prompt: str, label: str) -> Tuple[str, Optional[str], Dict[str, Any]]:
        """llama-server (OpenAI互換) へ1回POSTし、(content, finish_reason, usage) を返す。"""
        payload: Dict[str, Any] = {
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.0,
            "max_tokens": EXTRACTION_MAX_TOKENS,
            "model": settings.LLM_MODEL,
        }
        if self._json_mode:
            payload["response_format"] = {"type": "json_object"}

        headers = {
            "Authorization": f"Bearer {self.llm_api_key}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient() as client:
            try:
                resp = await client.post(self.llm_api_url, json=payload, headers=headers, timeout=LLM_TIMEOUT_SECONDS)
            except Exception as e:
                raise LLMExtractionError(f"transport_error: {e}") from e

            if resp.status_code in (400, 422) and self._json_mode:
                # llama-server が response_format に非対応だった場合は自動で無効化して1度だけやり直す
                logger.warning(
                    f"⚠️ response_format=json_object rejected (HTTP {resp.status_code}). Disabling JSON mode."
                )
                self._json_mode = False
                return await self._post_llm(prompt, label)

            try:
                resp.raise_for_status()
                body = resp.json()
                choice = (body.get("choices") or [{}])[0]
                message = choice.get("message") or {}
                content = (message.get("content") or "").strip()
                finish_reason = choice.get("finish_reason")
                usage = body.get("usage") or {}
                # 思考モデルで content が空の場合、reasoning_content に本文が入ることがある
                if not content and message.get("reasoning_content"):
                    logger.warning("⚠️ content is empty; falling back to reasoning_content for salvage.")
                    content = str(message["reasoning_content"]).strip()
            except Exception as e:
                raise LLMExtractionError(f"bad_response: {e}") from e

        logger.info(f"🤖 LLM_RAW[{label}] finish_reason={finish_reason} usage={usage} content={content[:600]!r}")
        return content, finish_reason, usage

    @observe(as_type="generation", capture_input=False, capture_output=False)
    async def _call_llm(self, item_name: str, context: str) -> Dict[str, Any]:
        """
        [Private] LLMにContextを渡し、JSON形式で日数リスト等を抽出させる。

        1回目が解釈不能なら、より短い指示で1回だけ再試行する。
        それでも駄目なら LLMExtractionError を送出する（黙って握りつぶさない）。
        """
        update_langfuse("generation", input={"item_name": item_name, "context_chars": len(context)})

        prompt = EXPIRATION_ESTIMATION_PROMPT.format(item_name=item_name, context_text=context)
        raw, finish_reason, usage = await self._post_llm(prompt, label="initial")
        data = _parse_llm_json(raw) or _salvage_llm_fields(raw)

        if _needs_retry(data):
            logger.warning(
                f"🔁 LLM出力が利用不可 (finish_reason={finish_reason}, "
                f"parsed={'yes' if data else 'no'}). 再試行します。"
            )
            retry_prompt = EXPIRATION_ESTIMATION_RETRY_PROMPT.format(item_name=item_name, context_text=context)
            retry_raw, retry_finish_reason, retry_usage = await self._post_llm(retry_prompt, label="retry")
            retried = _parse_llm_json(retry_raw) or _salvage_llm_fields(retry_raw)
            raw, finish_reason = retry_raw, retry_finish_reason
            usage = _merge_usage(usage, retry_usage)
            # 再試行の結果が1回目より良い場合のみ採用する（非食品への反転などで悪化させない）
            if retried is not None and _response_quality(retried) > _response_quality(data):
                data = retried

        if data is None:
            message = f"json_parse_failed (finish_reason={finish_reason})"
            update_langfuse(
                "generation",
                level="ERROR",
                status_message=message,
                output={"raw": raw[:1000], "finish_reason": finish_reason},
            )
            raise LLMExtractionError(message, raw=raw, finish_reason=finish_reason)

        update_langfuse(
            "generation",
            output=data,
            model=settings.LLM_MODEL,
            metadata={"finish_reason": finish_reason},
            usage_details=_usage_details(usage),
        )
        return data

    def _calculate_geometric_mean(self, days_list: List[float]) -> Optional[int]:
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


def _merge_usage(*usages: Dict[str, Any]) -> Dict[str, Any]:
    """複数回のLLM呼び出しのusageを合算する。"""
    merged: Dict[str, Any] = {}
    for usage in usages:
        for key, value in (usage or {}).items():
            if isinstance(value, (int, float)):
                merged[key] = merged.get(key, 0) + value
    return merged


def _usage_details(usage: Dict[str, Any]) -> Optional[Dict[str, int]]:
    """OpenAI互換のusageをLangfuseのusage_details形式へ変換する。"""
    if not usage:
        return None
    mapping = {
        "input": usage.get("prompt_tokens"),
        "output": usage.get("completion_tokens"),
        "total": usage.get("total_tokens"),
    }
    details = {key: int(value) for key, value in mapping.items() if isinstance(value, (int, float))}
    return details or None


def apply_estimation_outcome(dao: "InventoryDAO", item_name: str, outcome: EstimationOutcome) -> EstimationResult:
    """
    推定結果をDBへ反映する唯一の場所。

    dispatch直後(run_estimation_task) / 日次バックフィル / 手動スクリプトの
    3経路で分岐が食い違わないよう、ここに集約する。
    """
    status = outcome.status

    if status == EstimationResult.SUCCESS and outcome.data:
        dao.update_expiry(item_name, outcome.data["expiry_date"])
        logger.info(
            f"✅ Expiry Updated: {item_name} -> {outcome.data['expiry_date']} (約{outcome.data['days_offset']}日)"
        )

    elif status == EstimationResult.NON_FOOD:
        dao.mark_as_non_food(item_name)
        logger.info(f"🚫 Marked as Non-Food: {item_name}")

    elif status == EstimationResult.ERROR:
        reason = (outcome.reason or "unknown")[:200]
        dao.mark_estimation_failed(item_name, reason)
        logger.warning(f"⚠️ Estimation FAILED: {item_name} ({reason})")

    else:
        # SKIPPED: クォータ切れ等で実行していない。DBは変更せず次回に持ち越す。
        logger.info(f"⏭️ Estimation skipped (not attempted): {item_name} ({outcome.reason})")

    return status
