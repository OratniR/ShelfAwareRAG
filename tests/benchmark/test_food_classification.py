# tests/benchmark/test_food_classification.py
"""
食品判定の精度ベンチマーク。
LLMサーバーが起動している状態で実行する:
  pytest tests/benchmark/ -v -s -m benchmark
  pytest tests/benchmark/ -v -s -m benchmark --update-baseline  # ベースライン更新
"""

import asyncio
import json
import os
from collections import defaultdict
from pathlib import Path

import httpx
import pytest
from langfuse import get_client, observe

from shelf_aware.config import settings
from shelf_aware.estimation import ExpirationEstimator

from .food_classification_dataset import DATASET, WARMUP_ITEM

# ベースラインファイル
BASELINE_PATH = Path(__file__).parent / "baseline.json"

# カテゴリ順序
CATEGORY_ORDER = [
    "明らかな食品",
    "紛らわしい食品",
    "明らかな非食品",
    "紛らわしい非食品",
]


def _load_baseline() -> dict:
    """baseline.json からベースラインスコアを読み込む。"""
    if not BASELINE_PATH.exists():
        return {"accuracy": 0.0, "category_scores": {}}
    with open(BASELINE_PATH) as f:
        return json.load(f)


@pytest.fixture(scope="module")
def estimator():
    return ExpirationEstimator()


@pytest.fixture(scope="module")
def event_loop():
    """Module-scoped event loop for async fixtures."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


def _check_llm_server():
    """LLMサーバーの疎通確認。到達不可なら skip。"""
    url = settings.LLM_API_BASE.replace("/v1", "/v1/models")
    try:
        resp = httpx.get(url, timeout=5.0)
        resp.raise_for_status()
    except Exception:
        pytest.skip(f"LLM server is not reachable at {url}")


@pytest.mark.benchmark
class TestFoodClassificationBenchmark:
    """食品判定の精度を計測するベンチマークテスト。"""

    @pytest.fixture(autouse=True, scope="class")
    def setup(self, estimator):
        """LLMサーバーの疎通確認 + ウォームアップ。"""
        _check_llm_server()

        # --- ウォームアップ (コールドスタート回避) ---
        print(f"\n🔥 Warmup: classifying '{WARMUP_ITEM}'...")
        asyncio.get_event_loop().run_until_complete(estimator._classify_item_type(WARMUP_ITEM))
        print("✅ Warmup complete.\n")

        yield

        # --- Langfuse flush (テスト終了時に未送信イベントを送信) ---
        try:
            get_client().flush()
        except Exception:
            pass

    def test_benchmark(self, estimator, update_baseline):
        """全30問を実行し、ベースラインと比較して精度を検証する。"""
        loop = asyncio.get_event_loop()

        results = []  # (item_name, expected, actual, category, correct)
        category_stats = defaultdict(lambda: {"correct": 0, "total": 0})

        for item_name, expected_is_food, category in DATASET:
            response = loop.run_until_complete(self._classify_with_trace(estimator, item_name))
            actual_is_food = response.get("is_food", True)

            correct = actual_is_food == expected_is_food
            results.append((item_name, expected_is_food, actual_is_food, category, correct))

            category_stats[category]["total"] += 1
            if correct:
                category_stats[category]["correct"] += 1

            # 進捗表示
            icon = "✅" if correct else "❌"
            expected_label = "food" if expected_is_food else "non-food"
            actual_label = "food" if actual_is_food else "non-food"
            print(f"  {icon} {item_name}: expected={expected_label}, got={actual_label}")

        # === スコア算出 ===
        total_correct = sum(1 for *_, c in results if c)
        total = len(results)
        accuracy = total_correct / total * 100

        cat_scores = {}
        for cat in CATEGORY_ORDER:
            s = category_stats[cat]
            cat_scores[cat] = s["correct"] / s["total"] * 100 if s["total"] > 0 else 0

        failures = [(name, exp, act) for name, exp, act, _, correct in results if not correct]

        # === ベースラインとの比較 ===
        baseline = _load_baseline()
        baseline_accuracy = baseline.get("accuracy", 0.0)
        baseline_cat = baseline.get("category_scores", {})
        baseline_model = baseline.get("model", "unknown")

        regression = accuracy < baseline_accuracy
        regression_reasons = []

        if regression:
            regression_reasons.append(
                f"Total accuracy dropped: {baseline_accuracy:.1f}% → {accuracy:.1f}% "
                f"(Δ {accuracy - baseline_accuracy:+.1f}%)"
            )

        for cat in CATEGORY_ORDER:
            base_cat_score = baseline_cat.get(cat, 0.0)
            if cat_scores[cat] < base_cat_score:
                regression_reasons.append(
                    f"  {cat}: {base_cat_score:.1f}% → {cat_scores[cat]:.1f}% "
                    f"(Δ {cat_scores[cat] - base_cat_score:+.1f}%)"
                )

        # === レポート出力 ===
        report_lines = []
        report_lines.append("")
        report_lines.append("=" * 55)
        report_lines.append("  Food Classification Benchmark Results")
        report_lines.append("=" * 55)
        report_lines.append(f"  Model:    {settings.LLM_MODEL}")
        report_lines.append(f"  Baseline: {baseline_model} ({baseline_accuracy:.1f}%)")
        report_lines.append(f"  Current:  {total_correct}/{total} ({accuracy:.1f}%)")
        report_lines.append("")
        report_lines.append("  Category Breakdown:")

        for cat in CATEGORY_ORDER:
            s = category_stats[cat]
            base_s = baseline_cat.get(cat, 0.0)
            delta = cat_scores[cat] - base_s
            delta_str = f"  (Δ {delta:+.1f}%)" if base_s > 0 else ""
            report_lines.append(f"    {cat}:  {s['correct']}/{s['total']}  ({cat_scores[cat]:.1f}%){delta_str}")

        if failures:
            report_lines.append("")
            report_lines.append("  Failures:")
            for name, exp, act in failures:
                exp_label = "food" if exp else "non-food"
                act_label = "food" if act else "non-food"
                report_lines.append(f"    ❌ {name}: expected={exp_label}, got={act_label}")

        if regression_reasons:
            report_lines.append("")
            report_lines.append("  ⛔ REGRESSION DETECTED:")
            for reason in regression_reasons:
                report_lines.append(f"    {reason}")

        report_lines.append("=" * 55)
        report = "\n".join(report_lines)
        print(report)

        # --- GitHub Actions Job Summary ---
        summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary_file:
            with open(summary_file, "a") as f:
                status_icon = "✅" if not regression else "⛔"
                f.write(f"## {status_icon} Food Classification Benchmark\n\n")
                f.write(f"**Model**: `{settings.LLM_MODEL}`\n\n")
                f.write(f"**Accuracy**: {total_correct}/{total} ({accuracy:.1f}%)")
                f.write(f" — Baseline: {baseline_accuracy:.1f}% (`{baseline_model}`)\n\n")
                f.write("| カテゴリ | 正解率 | ベースライン | Δ |\n")
                f.write("|---------|--------|-------------|---|\n")
                for cat in CATEGORY_ORDER:
                    s = category_stats[cat]
                    base_s = baseline_cat.get(cat, 0.0)
                    delta = cat_scores[cat] - base_s
                    f.write(
                        f"| {cat} | {s['correct']}/{s['total']} ({cat_scores[cat]:.1f}%) "
                        f"| {base_s:.1f}% | {delta:+.1f}% |\n"
                    )
                if failures:
                    f.write("\n### ❌ Failures\n\n")
                    for name, exp, act in failures:
                        exp_label = "food" if exp else "non-food"
                        act_label = "food" if act else "non-food"
                        f.write(f"- **{name}**: expected={exp_label}, got={act_label}\n")
                if regression_reasons:
                    f.write("\n### ⛔ Regression\n\n")
                    for reason in regression_reasons:
                        f.write(f"- {reason}\n")

        # === ベースライン更新 (--update-baseline フラグ時) ===
        if update_baseline:
            new_baseline = {
                "model": settings.LLM_MODEL,
                "accuracy": accuracy,
                "category_scores": cat_scores,
            }
            with open(BASELINE_PATH, "w") as f:
                json.dump(new_baseline, f, ensure_ascii=False, indent=2)
            print(f"\n📝 Baseline updated: {BASELINE_PATH}")
            return  # ベースライン更新時は品質ゲートをスキップ

        # === 品質ゲート ===
        assert not regression, (
            f"⛔ Benchmark regression detected!\n"
            f"Baseline ({baseline_model}): {baseline_accuracy:.1f}%\n"
            f"Current  ({settings.LLM_MODEL}): {accuracy:.1f}%\n" + "\n".join(regression_reasons)
        )

    @observe(name="benchmark-classify")
    async def _classify_with_trace(self, estimator, item_name: str):
        """Langfuse トレース付きで食品判定を実行。"""
        return await estimator._classify_item_type(item_name)
