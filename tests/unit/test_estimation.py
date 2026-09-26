# tests/unit/test_estimation.py
"""
賞味期限推定パイプラインのユニットテスト。

外部依存 (Brave / LLMサーバー) は一切叩かない。
"""

import datetime as dt
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shelf_aware.estimation import (
    EstimationOutcome,
    EstimationResult,
    EstimationSkipped,
    ExpirationEstimator,
    LLMExtractionError,
    _build_search_context,
    _coerce_bool,
    _coerce_days,
    _extract_days_from_text,
    _find_duration_expressions,
    _needs_retry,
    _parse_is_food_text,
    _parse_llm_json,
    _parse_yes_no,
    _pick_expiry_sentences,
    _resolve_days,
    _response_quality,
    _salvage_llm_fields,
    apply_estimation_outcome,
    is_known_food,
)


@pytest.fixture
def estimator():
    est = ExpirationEstimator()
    # CIには .env が無いため、Braveキー未設定でSKIPPEDに落ちないようにする
    est.brave_api_key = "test-brave-key"
    return est


# --- ロジックのテスト (Mock不要) ---
def test_calculate_geometric_mean_normal(estimator):
    assert estimator._calculate_geometric_mean([3, 12]) == 6
    assert estimator._calculate_geometric_mean([2, 10]) == 4


def test_calculate_geometric_mean_single(estimator):
    assert estimator._calculate_geometric_mean([30]) == 30


def test_calculate_geometric_mean_edge_cases(estimator):
    assert estimator._calculate_geometric_mean([]) is None
    assert estimator._calculate_geometric_mean([0, -5]) is None


# --- 日本語の期間表現 → 日数 ---
def test_extract_days_from_text_units():
    assert _extract_days_from_text("未開封で1年です") == [365.0]
    assert _extract_days_from_text("半年持ちます") == [182.5]
    assert _extract_days_from_text("2ヶ月程度") == [60.0]
    assert _extract_days_from_text("1週間から10日") == [7.0, 10.0]


def test_extract_days_from_text_ignores_dates():
    # 「2026年3月1日」を保存期間と誤認しないこと
    assert _extract_days_from_text("賞味期限は2026年3月1日です") == []
    assert _extract_days_from_text("2026年") == []


def test_coerce_days_variants():
    assert _coerce_days([180, 365]) == [180.0, 365.0]
    assert _coerce_days(["180"]) == [180.0]
    assert _coerce_days(["1年", "半年"]) == [365.0, 182.5]
    assert _coerce_days("2ヶ月") == [60.0]
    assert _coerce_days(None) == []
    assert _coerce_days([]) == []
    assert _coerce_days([True, -5, 0]) == []
    # 常識外れ(10年超)は捨てる
    assert _coerce_days([99999]) == []


# --- 単位換算はLLMにやらせない（コード側で解決する） ---
def test_resolve_days_accepts_verbatim_periods():
    """LLMは表記そのままを返し、日数への換算はコードが行う。"""
    days, unresolved = _resolve_days({"is_food": True, "periods": ["2年", "半年"], "reason": "2年と半年"})

    assert days == [730.0, 182.5]
    assert unresolved == []


def test_resolve_days_reinterprets_unitless_number():
    """
    「2年」の "2" を 2日 として扱ってしまう事故の回帰テスト（お酢の事例）。

    LLMが extracted_days=[0, 2] / reason="未開封で2年という記述あり" を返しても 730日 になること。
    """
    data = {"is_food": True, "extracted_days": [0, 2], "reason": "未開封で2年という記述あり"}

    days, unresolved = _resolve_days(data)

    assert days == [730.0]
    assert unresolved == []


def test_resolve_days_reinterprets_months_and_weeks():
    assert _resolve_days({"is_food": True, "extracted_days": [6], "reason": "未開封で6ヶ月"})[0] == [180.0]
    assert _resolve_days({"is_food": True, "extracted_days": [2], "reason": "2週間ほど"})[0] == [14.0]


def test_resolve_days_keeps_day_unit_as_is():
    """「2日」と書いてある裸の数値は 2日 のまま（1倍なので解釈し直さない）。"""
    days, unresolved = _resolve_days({"is_food": True, "extracted_days": [2], "reason": "開封後は2日"})

    assert days == [2.0]
    assert unresolved == []


def test_resolve_days_keeps_large_unitless_number():
    """365以上の裸の数値は単位落ちの可能性が低いので、そのまま日数として扱う。"""
    days, unresolved = _resolve_days({"is_food": True, "extracted_days": [730], "reason": "長期間保存可能"})

    assert days == [730.0]
    assert unresolved == [730.0]


def test_resolve_days_marks_small_unitless_number_as_unresolved():
    days, unresolved = _resolve_days({"is_food": True, "extracted_days": [2], "reason": "長期間"})

    assert days == [2.0]
    assert unresolved == [2.0]


def test_find_duration_expressions_returns_value_and_days():
    assert _find_duration_expressions("未開封で2年") == [(2.0, 730.0)]
    assert _find_duration_expressions("賞味期限は2026年3月1日") == []


def test_response_quality_ranks_unit_resolved_highest():
    assert _response_quality(None) == 0
    assert _response_quality({"is_food": False}) == 1
    assert _response_quality({"is_food": True}) == 2
    assert _response_quality({"is_food": True, "extracted_days": [2]}) == 3
    assert _response_quality({"is_food": True, "periods": ["2年"]}) == 4
    assert _response_quality({"is_food": True, "extracted_days": [2], "reason": "2年"}) == 4


# --- 食品判定 (Phase 1) ---
def test_is_known_food_lexicon():
    """以前「たまに食べ物と判定されなかった」ものを辞書で確定させる。"""
    assert is_known_food("雑塩") is True
    assert is_known_food("豆腐") is True
    assert is_known_food("料理酒") is True  # 「料理酒のストック」からクリーニング後
    assert is_known_food("穀物酢") is True
    assert is_known_food("甜麺醤") is False  # 辞書に無いものはLLMに聞く

    # 非食品を食べ物と誤判定しないこと
    assert is_known_food("キーボード") is False
    assert is_known_food("手袋") is False
    assert is_known_food("印鑑") is False
    assert is_known_food("フライパン") is False  # 語尾が「パン」でも調理器具
    assert is_known_food("機械油") is False
    assert is_known_food("") is False


def test_parse_is_food_text_uses_last_occurrence():
    """
    制約文を復唱した出力で食べ物を非食品と誤判定しないこと。

    （「丸々のストックだとたまに食べ物と判定されない」の回帰テスト）
    """
    echo = "「food」または「non-food」の一単語のみで答えてください。豆腐はfoodです。"
    assert _parse_is_food_text(echo) is True
    assert _parse_is_food_text("non-food") is False
    assert _parse_is_food_text("これは食べられません") is None


def test_parse_yes_no():
    assert _parse_yes_no("はい") is True
    assert _parse_yes_no("はい、食べられます") is True
    assert _parse_yes_no("いいえ") is False
    assert _parse_yes_no("いいえ、食べられません") is False
    assert _parse_yes_no("non-food") is False
    assert _parse_yes_no("たぶん") is None


def test_coerce_bool():
    assert _coerce_bool(True) is True
    assert _coerce_bool(False) is False
    assert _coerce_bool("true") is True
    assert _coerce_bool("false") is False
    assert _coerce_bool("non-food") is False
    assert _coerce_bool("はい") is True
    assert _coerce_bool(1) is True
    assert _coerce_bool(0) is False
    assert _coerce_bool(None) is None
    assert _coerce_bool("たぶん") is None


@pytest.mark.asyncio
async def test_classify_uses_lexicon_without_llm(estimator):
    """辞書で確定できるものはLLMを呼ばない（Piの時間と誤判定を節約）。"""
    client, calls = _mock_client([('{"is_food": false}', "stop")])

    with patch("shelf_aware.estimation.httpx.AsyncClient", return_value=client):
        result = await estimator._classify_item_type("雑塩")

    assert result["is_food"] is True
    assert calls == []


@pytest.mark.asyncio
async def test_classify_confirms_non_food_verdict(estimator):
    """非食品判定は別の聞き方で確認し、2回とも非食品なら対象外にする。"""
    client, calls = _mock_client([('{"is_food": false}', "stop"), ("いいえ", "stop")])

    with patch("shelf_aware.estimation.httpx.AsyncClient", return_value=client):
        result = await estimator._classify_item_type("キーボード")

    assert result["is_food"] is False
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_classify_falls_back_to_food_when_not_confirmed(estimator):
    """判定が割れたら食べ物として扱う（誤って「対象外」に固定しない）。"""
    client, calls = _mock_client([('{"is_food": false}', "stop"), ("はい", "stop")])

    with patch("shelf_aware.estimation.httpx.AsyncClient", return_value=client):
        result = await estimator._classify_item_type("甜麺醤")

    assert result["is_food"] is True
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_classify_ignores_prompt_echo(estimator):
    """
    制約文を復唱しただけの出力で非食品と判定しないこと。

    （「◯◯のストックだとたまに食べ物と判定されない」の回帰テスト）
    """
    echo = "「food」または「non-food」の一単語のみで答えてください。玉ねぎはfoodです。"
    client, calls = _mock_client([(echo, "stop")])

    with patch("shelf_aware.estimation.httpx.AsyncClient", return_value=client):
        result = await estimator._classify_item_type("玉ねぎ")

    assert result["is_food"] is True
    assert len(calls) == 1  # 非食品ではないので確認は不要


# --- LLM出力の解釈 ---
def test_parse_llm_json_variants():
    assert _parse_llm_json('{"is_food": true, "extracted_days": [7]}') == {"is_food": True, "extracted_days": [7]}
    assert _parse_llm_json('```json\n{"is_food": true}\n```') == {"is_food": True}
    assert _parse_llm_json('はい。\n{"is_food": true, "extracted_days": [30]}\n以上です。')["extracted_days"] == [30]
    assert _parse_llm_json("") is None
    assert _parse_llm_json("豆板醤は食品です。") is None


def test_salvage_truncated_json():
    """max_tokensで切れたJSONから日数を救出できること（今回の障害の直接原因）。"""
    raw = '{"is_food": true, "extracted_days": [180, 365], "reason": "未開封で1年程度と'
    salvaged = _salvage_llm_fields(raw)
    assert salvaged is not None
    assert salvaged["is_food"] is True
    assert salvaged["extracted_days"] == [180.0, 365.0]


def test_salvage_keeps_non_food_judgement():
    salvaged = _salvage_llm_fields('{"is_food": false, "extracted_days": [], "reason": "対象外')
    assert salvaged["is_food"] is False


def test_salvage_returns_none_for_prose():
    # 地の文からは推測しない（誤推定を避ける）
    assert _salvage_llm_fields("豆板醤は食品です。未開封で1年持ちます。") is None


def test_needs_retry():
    assert _needs_retry(None) is True
    assert _needs_retry({"is_food": False}) is False
    # 食品なのに日数が無い = 再試行対象（以前はここがSKIPPEDに化けていた）
    assert _needs_retry({"is_food": True}) is True
    assert _needs_retry({"is_food": True, "extracted_days": []}) is True
    # 単位付きなら確定できるので再試行しない
    assert _needs_retry({"is_food": True, "periods": ["2年"]}) is False
    # 大きい裸の数値は日数として妥当（365日/730日など）なので再試行しない
    assert _needs_retry({"is_food": True, "extracted_days": [365]}) is False
    # 小さい裸の数値は単位を落としている疑いが強いので聞き直す
    assert _needs_retry({"is_food": True, "extracted_days": [2]}) is True
    assert _needs_retry({"is_food": True, "extracted_days": [0, 2], "reason": "未開封で2年という記述あり"}) is False


# --- 検索結果の整形 ---
def test_pick_expiry_sentences_keeps_numbers():
    text = "当社の豆板醤は未開封で1年、開封後は冷蔵で3ヶ月です。ラーメンにも使えます。"
    picked = _pick_expiry_sentences(text)
    assert "1年" in picked
    assert _extract_days_from_text(picked) == [365.0, 90.0]


def test_build_search_context_keeps_shelf_life_info():
    """以前は desc[:80] で切って数値が消えていた（回帰テスト）。"""
    results = [
        {
            "title": "豆板醤が腐るとどうなる？期限切れ半年･1年は危険？未開封はOK？",
            "description": (
                "<strong>豆板醤</strong>の賞味期限は未開封で1年程度です。"
                "開封後は冷蔵庫で保存し、なるべく早く使い切ってください。"
            ),
        }
    ]
    context = _build_search_context(results)
    assert "豆板醤" in context
    # タイトルと本文の両方から期間表現が拾えていること（以前は80文字で切られて消えていた）
    assert 365.0 in _extract_days_from_text(context)


def test_build_search_context_respects_budget():
    results = [{"title": f"title{i}", "description": "賞味期限は1年です。" * 20} for i in range(30)]
    assert len(_build_search_context(results, budget=300)) <= 300


# --- LLM呼び出し (HTTPはモック) ---
def _mock_client(responses):
    """responses: [(content, finish_reason), ...] を順に返すモッククライアント。"""
    calls = []

    async def fake_post(url, json=None, headers=None, timeout=None):
        calls.append(json)
        index = min(len(calls) - 1, len(responses) - 1)
        content, finish_reason = responses[index]
        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status.return_value = None
        resp.json.return_value = {
            "choices": [{"message": {"content": content}, "finish_reason": finish_reason}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
        }
        return resp

    client = AsyncMock()
    client.post = AsyncMock(side_effect=fake_post)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client, calls


@pytest.mark.asyncio
async def test_call_llm_clean_json(estimator):
    client, calls = _mock_client([('{"is_food": true, "periods": ["1週間"], "reason": "ok"}', "stop")])

    with patch("shelf_aware.estimation.httpx.AsyncClient", return_value=client):
        result = await estimator._call_llm("納豆", "context")

    assert result["is_food"] is True
    assert _resolve_days(result)[0] == [7.0]
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_call_llm_reasks_when_unit_is_missing(estimator):
    """
    単位の無い小さい数値が返ったら、表記そのままを求めて聞き直し、単位付きの回答を採用する。
    （「2年」の 2 を 2日 として確定しないため）
    """
    client, calls = _mock_client(
        [
            ('{"is_food": true, "extracted_days": [2], "reason": "長持ちする"}', "stop"),
            ('{"is_food": true, "periods": ["2年"], "reason": "2年"}', "stop"),
        ]
    )

    with patch("shelf_aware.estimation.httpx.AsyncClient", return_value=client):
        result = await estimator._call_llm("お酢", "context")

    assert len(calls) == 2
    assert _resolve_days(result)[0] == [730.0]


@pytest.mark.asyncio
async def test_call_llm_keeps_better_first_result(estimator):
    """再試行が非食品などを返しても、1回目の有効な結果を捨てないこと。"""
    client, calls = _mock_client(
        [
            ('{"is_food": true, "extracted_days": [2], "reason": "長持ちする"}', "stop"),
            ('{"is_food": false, "periods": [], "reason": "不明"}', "stop"),
        ]
    )

    with patch("shelf_aware.estimation.httpx.AsyncClient", return_value=client):
        result = await estimator._call_llm("お酢", "context")

    assert len(calls) == 2
    assert result["is_food"] is True
    assert _resolve_days(result)[0] == [2.0]


@pytest.mark.asyncio
async def test_call_llm_retries_once_then_succeeds(estimator):
    client, calls = _mock_client(
        [
            ("", "length"),  # 1回目: 空応答
            ('{"is_food": true, "extracted_days": [180]}', "stop"),  # 2回目: 成功
        ]
    )

    with patch("shelf_aware.estimation.httpx.AsyncClient", return_value=client):
        result = await estimator._call_llm("豆板醤", "context")

    assert result["extracted_days"] == [180]
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_call_llm_raises_when_unparseable(estimator):
    """解釈不能を {'is_food': True} に化けさせず、例外で知らせること（今回の障害の回帰テスト）。"""
    client, calls = _mock_client([("豆板醤は食品です。", "stop"), ("JSONでは出力できません。", "stop")])

    with patch("shelf_aware.estimation.httpx.AsyncClient", return_value=client):
        with pytest.raises(LLMExtractionError):
            await estimator._call_llm("豆板醤", "context")

    assert len(calls) == 2


@pytest.mark.asyncio
async def test_call_llm_salvages_truncated_output(estimator):
    client, _ = _mock_client([('{"is_food": true, "extracted_days": [180, 365], "reason": "未開封で1年', "length")])

    with patch("shelf_aware.estimation.httpx.AsyncClient", return_value=client):
        result = await estimator._call_llm("豆板醤", "context")

    assert result["extracted_days"] == [180.0, 365.0]


@pytest.mark.asyncio
async def test_call_llm_disables_json_mode_when_server_rejects_it(estimator):
    """llama-server が response_format 非対応でも自動で通常モードに落ちて動くこと。"""
    payloads = []

    async def fake_post(url, json=None, headers=None, timeout=None):
        payloads.append(json)
        resp = MagicMock()
        if len(payloads) == 1:
            resp.status_code = 400
            resp.raise_for_status.return_value = None
            resp.json.return_value = {"error": "response_format is not supported"}
            return resp
        resp.status_code = 200
        resp.raise_for_status.return_value = None
        resp.json.return_value = {"choices": [{"message": {"content": '{"is_food": true, "extracted_days": [30]}'}}]}
        return resp

    client = AsyncMock()
    client.post = AsyncMock(side_effect=fake_post)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    with patch("shelf_aware.estimation.httpx.AsyncClient", return_value=client):
        result = await estimator._call_llm("納豆", "context")

    assert result["extracted_days"] == [30]
    assert estimator._json_mode is False
    assert "response_format" in payloads[0]
    assert "response_format" not in payloads[1]


# --- パイプライン全体 (Mock) ---
@pytest.mark.asyncio
async def test_estimate_expiration_success(estimator):
    estimator._classify_item_type = AsyncMock(return_value={"is_food": True})
    estimator._search_brave = AsyncMock(return_value="- 豆板醤: 未開封で1年程度")
    estimator._call_llm = AsyncMock(return_value={"is_food": True, "extracted_days": [365], "reason": "1年"})

    outcome = await estimator.estimate_expiration("豆板醤", MagicMock())

    assert outcome.status == EstimationResult.SUCCESS
    assert outcome.data["days_offset"] == 365


@pytest.mark.asyncio
async def test_estimate_expiration_non_food(estimator):
    estimator._classify_item_type = AsyncMock(return_value={"is_food": False})
    estimator._search_brave = AsyncMock()

    outcome = await estimator.estimate_expiration("ドライヤー", MagicMock())

    assert outcome.status == EstimationResult.NON_FOOD
    estimator._search_brave.assert_not_awaited()


@pytest.mark.asyncio
async def test_estimate_expiration_skipped_on_quota(estimator):
    estimator._classify_item_type = AsyncMock(return_value={"is_food": True})
    estimator._search_brave = AsyncMock(side_effect=EstimationSkipped("brave_monthly_limit"))
    estimator._call_llm = AsyncMock()

    outcome = await estimator.estimate_expiration("豆板醤", MagicMock())

    assert outcome.status == EstimationResult.SKIPPED
    assert outcome.reason == "brave_monthly_limit"
    estimator._call_llm.assert_not_awaited()


@pytest.mark.asyncio
async def test_estimate_expiration_reports_error_when_no_days():
    """
    「検索は成功したが日数が取れなかった」を SKIPPED ではなく ERROR にすること。
    これが今回の『ダッシュボードに反映されない』障害の核心。
    """
    estimator = ExpirationEstimator()
    estimator.brave_api_key = "test-brave-key"
    estimator._classify_item_type = AsyncMock(return_value={"is_food": True})
    estimator._search_brave = AsyncMock(return_value="- 豆板醤: 賞味期限はどれくらい？")
    estimator._call_llm = AsyncMock(return_value={"is_food": True})  # 日数が無い

    outcome = await estimator.estimate_expiration("豆板醤", MagicMock())

    assert outcome.status == EstimationResult.ERROR
    assert outcome.reason == "no_days_extracted"


@pytest.mark.asyncio
async def test_estimate_expiration_reports_error_when_llm_fails(estimator):
    estimator._classify_item_type = AsyncMock(return_value={"is_food": True})
    estimator._search_brave = AsyncMock(return_value="- 豆板醤: 何か")
    estimator._call_llm = AsyncMock(side_effect=LLMExtractionError("json_parse_failed"))

    outcome = await estimator.estimate_expiration("豆板醤", MagicMock())

    assert outcome.status == EstimationResult.ERROR
    assert "llm_extraction_failed" in outcome.reason


@pytest.mark.asyncio
async def test_estimate_expiration_converts_years_not_days():
    """
    「未開封で2年」を 2日 と解釈してしまう事故の回帰テスト（お酢の事例）。

    LLMが extracted_days=[0, 2] を返しても、reason の「2年」から 730日 として解決すること。
    """
    estimator = ExpirationEstimator()
    estimator.brave_api_key = "test-brave-key"
    estimator._classify_item_type = AsyncMock(return_value={"is_food": True})
    estimator._search_brave = AsyncMock(return_value="- お酢: 未開封なら2年間")
    estimator._call_llm = AsyncMock(
        return_value={"is_food": True, "extracted_days": [0, 2], "reason": "未開封で2年という記述あり"}
    )

    outcome = await estimator.estimate_expiration("お酢", MagicMock())

    assert outcome.status == EstimationResult.SUCCESS
    assert outcome.data["days_offset"] == 730
    expected = (dt.datetime.now() + dt.timedelta(days=730)).date().isoformat()
    assert outcome.data["expiry_date"] == expected


@pytest.mark.asyncio
async def test_estimate_expiration_uses_verbatim_periods():
    """プロンプトを直した後の経路: periods に表記そのままが入っていればコード側で換算する。"""
    estimator = ExpirationEstimator()
    estimator.brave_api_key = "test-brave-key"
    estimator._classify_item_type = AsyncMock(return_value={"is_food": True})
    estimator._search_brave = AsyncMock(return_value="- お酢: 未開封なら2年間")
    estimator._call_llm = AsyncMock(return_value={"is_food": True, "periods": ["2年"], "reason": "2年間"})

    outcome = await estimator.estimate_expiration("お酢", MagicMock())

    assert outcome.status == EstimationResult.SUCCESS
    assert outcome.data["days_offset"] == 730


# --- DBへの反映 (結果 → ステータス) ---
def test_apply_outcome_success():
    dao = MagicMock()
    outcome = EstimationOutcome(EstimationResult.SUCCESS, data={"expiry_date": "2027-03-01", "days_offset": 365})

    assert apply_estimation_outcome(dao, "豆板醤", outcome) == EstimationResult.SUCCESS
    dao.update_expiry.assert_called_once_with("豆板醤", "2027-03-01")


def test_apply_outcome_non_food():
    dao = MagicMock()
    apply_estimation_outcome(dao, "ドライヤー", EstimationOutcome(EstimationResult.NON_FOOD))
    dao.mark_as_non_food.assert_called_once_with("ドライヤー")


def test_apply_outcome_error_marks_failed():
    """失敗は is_estimated=3 として記録される（未処理のまま放置しない）。"""
    dao = MagicMock()
    apply_estimation_outcome(dao, "豆板醤", EstimationOutcome(EstimationResult.ERROR, reason="no_days_extracted"))
    dao.mark_estimation_failed.assert_called_once_with("豆板醤", "no_days_extracted")


def test_apply_outcome_skipped_does_not_touch_db():
    dao = MagicMock()
    apply_estimation_outcome(dao, "豆板醤", EstimationOutcome(EstimationResult.SKIPPED, reason="brave_monthly_limit"))
    dao.update_expiry.assert_not_called()
    dao.mark_as_non_food.assert_not_called()
    dao.mark_estimation_failed.assert_not_called()
