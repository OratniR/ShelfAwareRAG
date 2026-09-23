"""
テスト環境に無い重い依存 (chromadb / sentence_transformers) をスタブ化するヘルパー。

Raspberry Pi や CI のように実際にインストール済みの環境では何もしない。
ローカルの軽量な仮想環境でも `shelf_aware.database` をimportして
SQLiteまわりのロジックをテストできるようにするためのもの。
"""

import sys
import types
from unittest.mock import MagicMock


def install_heavy_stubs() -> None:
    if "chromadb" not in sys.modules:
        try:
            import chromadb  # noqa: F401
        except Exception:
            chromadb = types.ModuleType("chromadb")
            chromadb.PersistentClient = MagicMock()
            api = types.ModuleType("chromadb.api")
            api_types = types.ModuleType("chromadb.api.types")

            class _EmbeddingFunction:
                pass

            api_types.Documents = list
            api_types.Embeddings = list
            api_types.EmbeddingFunction = _EmbeddingFunction
            chromadb.api = api
            sys.modules["chromadb"] = chromadb
            sys.modules["chromadb.api"] = api
            sys.modules["chromadb.api.types"] = api_types

    if "sentence_transformers" not in sys.modules:
        try:
            import sentence_transformers  # noqa: F401
        except Exception:
            sentence_transformers = types.ModuleType("sentence_transformers")
            sentence_transformers.SentenceTransformer = MagicMock()
            sys.modules["sentence_transformers"] = sentence_transformers
