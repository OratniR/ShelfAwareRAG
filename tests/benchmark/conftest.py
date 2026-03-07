# tests/benchmark/conftest.py
"""
ベンチマーク用の conftest。
--update-baseline オプション or UPDATE_BASELINE=1 環境変数で baseline.json を更新。
"""

import os

import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--update-baseline",
        action="store_true",
        default=False,
        help="Update baseline.json with results from this run",
    )


@pytest.fixture(scope="session")
def update_baseline(request):
    # CLI フラグ or 環境変数のどちらかで有効化
    return request.config.getoption("--update-baseline") or os.environ.get("UPDATE_BASELINE") == "1"
