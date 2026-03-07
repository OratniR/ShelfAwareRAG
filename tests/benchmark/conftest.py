# tests/benchmark/conftest.py
"""
ベンチマーク用の conftest。
--update-baseline オプションで baseline.json を現在の結果で更新できる。
"""

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
    return request.config.getoption("--update-baseline")
