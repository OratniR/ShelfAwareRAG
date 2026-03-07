# tests/benchmark/food_classification_dataset.py
"""
食品判定ベンチマーク用データセット (30問)
各テストケースは (アイテム名, 期待するis_food, カテゴリ) のタプル。
"""

# --- カテゴリ定数 ---
OBVIOUS_FOOD = "明らかな食品"
TRICKY_FOOD = "紛らわしい食品"
OBVIOUS_NON_FOOD = "明らかな非食品"
TRICKY_NON_FOOD = "紛らわしい非食品"

# --- データセット ---
# (item_name, expected_is_food, category)
DATASET: list[tuple[str, bool, str]] = [
    # ===== 明らかな食品 (8問) =====
    ("牛乳", True, OBVIOUS_FOOD),
    ("納豆", True, OBVIOUS_FOOD),
    ("味噌", True, OBVIOUS_FOOD),
    ("米", True, OBVIOUS_FOOD),
    ("卵", True, OBVIOUS_FOOD),
    ("バター", True, OBVIOUS_FOOD),
    ("食パン", True, OBVIOUS_FOOD),
    ("ヨーグルト", True, OBVIOUS_FOOD),
    # ===== 紛らわしい食品 (7問) =====
    # これらは食品だが、モデルが non-food と誤判定しがち
    ("豆腐", True, TRICKY_FOOD),
    ("コチュジャン", True, TRICKY_FOOD),
    ("ナンプラー", True, TRICKY_FOOD),
    ("粉ゼラチン", True, TRICKY_FOOD),
    ("オイスターソース", True, TRICKY_FOOD),
    ("甜麺醤", True, TRICKY_FOOD),
    ("柚子胡椒", True, TRICKY_FOOD),
    # ===== 明らかな非食品 (8問) =====
    ("フライパン", False, OBVIOUS_NON_FOOD),
    ("食器用洗剤", False, OBVIOUS_NON_FOOD),
    ("電池", False, OBVIOUS_NON_FOOD),
    ("アルミホイル", False, OBVIOUS_NON_FOOD),
    ("ゴム手袋", False, OBVIOUS_NON_FOOD),
    ("スポンジ", False, OBVIOUS_NON_FOOD),
    ("ラップ", False, OBVIOUS_NON_FOOD),
    ("キッチンペーパー", False, OBVIOUS_NON_FOOD),
    # ===== 紛らわしい非食品 (7問) =====
    # これらは非食品だが、モデルが food と誤判定しがち
    ("サラダスピナー", False, TRICKY_NON_FOOD),
    ("レンジフード", False, TRICKY_NON_FOOD),
    ("キッチンスケール", False, TRICKY_NON_FOOD),
    ("フードプロセッサー", False, TRICKY_NON_FOOD),
    ("ミルクフォーマー", False, TRICKY_NON_FOOD),
    ("エッグスライサー", False, TRICKY_NON_FOOD),
    ("ポテトマッシャー", False, TRICKY_NON_FOOD),
]

# ウォームアップ用ダミーアイテム
WARMUP_ITEM = "りんご"
