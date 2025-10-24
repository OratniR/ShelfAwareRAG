# src/shelf_aware/prompts.py

INTENT_CLASSIFICATION_SYSTEM_PROMPT = """
<task_description>
あなたはユーザーの発言を分析し、意図（intent）を分類し、関連情報を抽出して、指定されたJSON形式 *のみ* で出力するボットです。他のテキストは絶対に含めないでください。
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