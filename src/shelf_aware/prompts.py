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
2. 期間に幅がある場合や、複数の情報源がある場合は、言及されている期間をすべて periods に含めてください。
3. 文脈から判断して、「{item_name}」が明らかに食品、飲料、調味料でない（道具や機械など）場合も、"is_food": false を返してください。
4. 重要: 検索結果が「缶詰一般」「レトルト食品一般」の話しかしておらず、「{item_name}」について具体的に触れていない場合は、"is_food": false を返してください（情報の信頼性が低いため）。
5. periods には、検索結果に出てきた期間の表記を**そのまま**入れてください（例: "2年", "半年", "1ヶ月", "1週間", "10日"）。
   数値だけに変換してはいけません（"2年" を 2 にしてはいけない）。日数への換算も不要です。
6. 検索結果に具体的な期間が書かれていない場合は periods を [] にしてください。推測で数値を作ってはいけません。
7. is_food が true のときも periods と reason のキーは必ず含めてください。

# 出力形式（厳守）
- JSONオブジェクトを1つだけ出力する。前後に説明文・挨拶・Markdownのコードフェンス(```)を書かない。
- periods は「文字列」の配列にする（数値の配列にしない）。
- reason は日本語で40文字以内。
- 下の出力例の値をそのまま使わないこと。必ずContextの記述に基づくこと。

# 出力例
Input: 納豆
Context: ...納豆の賞味期限は冷蔵で1週間から10日程度です...
Output: {{"is_food": true, "periods": ["1週間", "10日"], "reason": "冷蔵で1週間〜10日という記述より"}}
---
# Item
{item_name}

# Context
{context_text}

# Output (JSONのみ)
"""


# 1回目の出力がJSONとして解釈できなかった場合の再試行用プロンプト。
# 例示を最小限にして出力トークンを節約する（Raspberry Pi では1回の生成が20〜40秒かかるため）。
EXPIRATION_ESTIMATION_RETRY_PROMPT = """
検索結果から保存期間を抽出し、JSONオブジェクトを1つだけ出力してください。
説明・挨拶・Markdown・コードフェンスは一切書かず、1行で出力してください。

キー:
- is_food: 「{item_name}」が食品・飲料・調味料なら true、食べられないものなら false
- periods: 検索結果に出てきた期間の表記をそのまま入れた「文字列」の配列
  （例: "2年", "半年", "1ヶ月", "1週間", "10日"）。数値だけにはしない。日数への換算は不要。記載がなければ []
- reason: 日本語40文字以内

# Item
{item_name}

# Context
{context_text}

# Output (JSONのみ・1行)
"""

# src/shelf_aware/prompts.py

# ... (既存の EXPIRATION_ESTIMATION_PROMPT はそのまま) ...

FOOD_CLASSIFICATION_PROMPT = """
あなたはルールに厳格なアシスタントです。
アイテム名「{item_name}」が、人間が食べるための「food」かどうかを判定してください。

【判定ルール】
food: 人間が口に入れる食材、調味料、お菓子、飲み物。
non-food: 調理器具、キッチン消耗品、文房具、家電、その他食べられないもの全て。

制約
「food」または「non-food」の一単語のみで答えてください。
これ以外は絶対に出力しないこと

対象アイテム: {item_name}
回答:"""
