```mermaid
useCaseDiagram
    actor ユーザー
    rectangle ShelfAwareRAGシステム {
        usecase UC1 as "アイテムの場所を登録する"
        usecase UC2 as "アイテムの場所を検索する"
        usecase UC3 as "アイテムの情報を削除する"
    }
    ユーザー -- UC1
    ユーザー -- UC2
    ユーザー -- UC3
```