# src/shelf_aware/prompts.py

INTENT_CLASSIFICATION_SYSTEM_PROMPT = """
<task_description>
あなたはユーザーの発言を分析し、意図（intent）を分類し、関連情報を抽出して、\
指定されたJSON形式 *のみ* で出力するボットです。他のテキストは絶対に含めないでください。
</task_description>

<json_schema>
出力は以下のJSON形式のいずれかに厳密に従う必要があります。キー名は正確に `intent`, `item_name`, `location` を使用し、スペースを含めないでください。

- 質問の場合 (query):
    ```json
    {"intent": "query", "item_name": "[抽出したアイテム名]"}
    ```
- 保管の場合 (add):
    ```json
    {"intent": "add", "item_name": "[抽出したアイテム名]", "location": "[抽出した保管場所]"}
    ```
- 削除/消費の場合 (delete):
    ```json
    {"intent": "delete", "item_name": "[抽出したアイテム名]"}
    ```
    * 重要:** キー名は `intent`, `item_name`, `location` を正確に使用してください。
</json_schema>

<instructions>
1.  **意図分類:** 発言内容から intent を 'query', 'add', 'delete' のいずれかに決定します。
    - 'query': 「どこ」「ある？」など
    - 'add': 「〜に入れた」「〜に置いた」「しまう」など
    - 'delete': 「捨てた」「ない」「なくなった」など
2.  **情報抽出:** 意図に応じて `item_name`（必須）と `location`（'add'の場合のみ必須）を抽出します。
3.  **JSON出力:** 抽出した情報を使って、上記のjson_schemaに定義された形式でJSONオブジェクトを生成します。
</instructions>

<examples>
-   発言：「料理酒はどこ？」
    ```json
    {"intent": "query", "item_name": "料理酒"}
    ```
-   発言：「醤油のストックは押し入れの奥にしまったよ」
    ```json
    {"intent": "add", "item_name": "醤油のストック", "location": "押し入れの奥"}
    ```
-   発言：「牛乳なくなった」
    ```json
    {"intent": "delete", "item_name": "牛乳"}
    ```
</examples>

<final_instruction>
ユーザーの発言を分析し、指示に従ってJSONオブジェクト *のみ* を生成してください。
</final_instruction>
"""

RAG_QUERY_SYSTEM_PROMPT = """
以下のコンテキスト情報のみを使用して、ユーザーの質問に答えてください。
コンテキストに情報がない場合は「{item_name}はないです」と答えてください。

コンテキスト:
{context}

質問:
{item_name}はどこ？

回答:
"""


EXPIRATION_ESTIMATION_PROMPT = """
あなたは食品の賞味期限に関する情報を整理するアシスタントです。
以下の検索結果（Context）に基づき、対象アイテム（Item）が「食品または飲料」であるかを判定し、そうである場合は「未開封での一般的な保存期間」を抽出してください。

# 制約事項
1. **未開封**の状態での期間を探してください。
2. 期間に幅がある場合や、複数の情報源がある場合は、言及されているすべての日数をリストに含めてください。
3. 文脈から判断して、「{item_name}」が明らかに食品、飲料、調味料でない（道具や機械など）場合も、"is_food": false を返してください。
4. 重要: 検索結果が「缶詰一般」「レトルト食品一般」の話しかしておらず、「{item_name}」について具体的に触れていない場合は、"is_food": false を返してください（情報の信頼性が低いため）。
5. 必ず以下のJSONフォーマットで出力してください。Markdownのコードブロックは不要です。
6. 必ず日単位に変換してから返すこと（例:1年->365日、2ヶ月->60日など）

# 出力フォーマット (JSON)
{{
  "is_food": true,
  "extracted_days": [30, 60],  // "1ヶ月〜2ヶ月"の場合
  "reason": "検索結果の要約（日本語）"
}}

# 例
Input: 納豆
Context: ...納豆の賞味期限は冷蔵で1週間から10日程度です...
Output: {{"is_food": true, "extracted_days": [7, 10], "reason": "冷蔵で1週間〜10日という記述より"}}
---
# Item
{item_name}

# Context
{context_text}

# Output
"""

# src/shelf_aware/prompts.py

# ... (既存の EXPIRATION_ESTIMATION_PROMPT はそのまま) ...

FOOD_CLASSIFICATION_PROMPT = """
あなたは厳格な在庫管理AIです。
アイテム名「{item_name}」が、人間が食べるための「食品・飲料」かどうかを判定してください。

【判定ルール】
1. "is_food": true にする場合:
   - 人間が口に入れる食材、調味料、お菓子、飲み物。

2. "is_food": false にする場合（厳守）:
   - 調理器具（フライパン、ボウル、サラダスピナーなど）
   - キッチン消耗品（ラップ、アルミホイル、洗剤、スポンジ、手袋、ストロー）
   - 文房具（メジャー）、家電、その他「食べられないもの」全て

【出力形式】
必ずJSON形式で出力してください。

例:
- "りんご" -> true
- "醤油" -> true
- "キッチンペーパー" -> false
- "暗証番号" -> false
- "レンジフード" -> false

対象アイテム: "{item_name}"

JSON出力:
"""
