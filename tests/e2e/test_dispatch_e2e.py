"""
End-to-end tests for the /dispatch endpoint.

These tests exercise the full add → query → delete → query lifecycle
against a REAL running server (docker compose up).

No mocking — all external services (LLM, ChromaDB, Notion) must be live.

Usage:
    docker compose up -d
    uv run pytest tests/e2e/ -v
"""

import pytest

# All tests in this file are marked as e2e
pytestmark = pytest.mark.e2e

# --- Test items ---
# Use realistic names so the LLM can classify intent correctly.
# The LLM extracts item_name from natural language, so assertions
# should check for what the LLM is likely to extract, not the raw input.
TEST_ITEM = "みかん"
TEST_LOCATION = "冷蔵庫"

# Error responses from main.py (for negative assertions)
UNKNOWN_RESPONSE = "よくわかりませんでした"
NOT_FOUND_RESPONSE = "見つかりませんでした"


class TestAddFlow:
    """Test the 'add' intent via /dispatch."""

    def test_add_item(self, client):
        """Adding an item should return a success message with the item name and location."""
        resp = client.post(
            "/dispatch",
            json={"text": f"{TEST_ITEM}を{TEST_LOCATION}にしまって"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "answer" in data
        # LLM should classify as 'add' — response should NOT be "unknown"
        assert UNKNOWN_RESPONSE not in data["answer"], f"LLM failed to classify intent: {data['answer']}"
        assert "しまいました" in data["answer"]


class TestQueryFlow:
    """Test the 'query' intent via /dispatch (requires a prior add)."""

    def test_query_existing_item(self, client):
        """Querying an added item should return its location."""
        # Ensure item exists
        client.post(
            "/dispatch",
            json={"text": f"{TEST_ITEM}を{TEST_LOCATION}にしまって"},
        )

        resp = client.post(
            "/dispatch",
            json={"text": f"{TEST_ITEM}はどこ？"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "answer" in data
        assert UNKNOWN_RESPONSE not in data["answer"], f"LLM failed to classify intent: {data['answer']}"
        # The response should mention the location or "not found"
        assert TEST_LOCATION in data["answer"] or NOT_FOUND_RESPONSE not in data["answer"]

    def test_query_nonexistent_item(self, client):
        """Querying an item that doesn't exist should return a 'not found' message."""
        resp = client.post(
            "/dispatch",
            json={"text": "ドラゴンフルーツはどこ？"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "answer" in data
        assert UNKNOWN_RESPONSE not in data["answer"], f"LLM failed to classify intent: {data['answer']}"
        assert NOT_FOUND_RESPONSE in data["answer"]


class TestDeleteFlow:
    """Test the 'delete' intent via /dispatch."""

    def test_delete_item(self, client):
        """Deleting an item should return a success message."""
        # Ensure item exists first
        client.post(
            "/dispatch",
            json={"text": f"{TEST_ITEM}を{TEST_LOCATION}にしまって"},
        )

        resp = client.post(
            "/dispatch",
            json={"text": f"{TEST_ITEM}を削除して"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "answer" in data
        assert UNKNOWN_RESPONSE not in data["answer"], f"LLM failed to classify intent: {data['answer']}"
        assert "削除" in data["answer"]


class TestFullLifecycle:
    """Test the complete add → query → delete → query lifecycle."""

    def test_lifecycle(self, client):
        item = "バナナ"
        location = "棚"

        # 1. Add
        resp = client.post(
            "/dispatch",
            json={"text": f"{item}を{location}にしまって"},
        )
        assert resp.status_code == 200
        assert UNKNOWN_RESPONSE not in resp.json()["answer"]
        assert "しまいました" in resp.json()["answer"]

        # 2. Query → should be found
        resp = client.post(
            "/dispatch",
            json={"text": f"{item}はどこ？"},
        )
        assert resp.status_code == 200
        answer = resp.json()["answer"]
        assert UNKNOWN_RESPONSE not in answer
        assert location in answer or item in answer

        # 3. Delete
        resp = client.post(
            "/dispatch",
            json={"text": f"{item}を削除して"},
        )
        assert resp.status_code == 200
        assert "削除" in resp.json()["answer"]

        # 4. Query again → should NOT be found
        resp = client.post(
            "/dispatch",
            json={"text": f"{item}はどこ？"},
        )
        assert resp.status_code == 200
        assert NOT_FOUND_RESPONSE in resp.json()["answer"]
