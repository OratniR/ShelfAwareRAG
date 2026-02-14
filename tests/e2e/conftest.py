import logging

import httpx
import pytest

BASE_URL = "http://localhost:8000"
logger = logging.getLogger(__name__)


@pytest.fixture(scope="session")
def base_url():
    return BASE_URL


@pytest.fixture(scope="session", autouse=True)
def _check_server_reachable():
    """Auto-skip all e2e tests if the local server is not running."""
    try:
        resp = httpx.get(f"{BASE_URL}/", timeout=3.0)
        resp.raise_for_status()
    except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPStatusError):
        pytest.skip(
            f"Server at {BASE_URL} is not reachable. Start it with `docker compose up` before running e2e tests."
        )


@pytest.fixture(scope="session", autouse=True)
def _warmup_llm(_check_server_reachable):
    """Send a warm-up request to load the LLM model before real tests run."""
    logger.info("Warming up LLM with a dummy request (this may take a few minutes)...")
    try:
        with httpx.Client(base_url=BASE_URL, timeout=180.0) as c:
            c.post("/dispatch", json={"text": "テスト"})
        logger.info("LLM warm-up complete.")
    except httpx.ReadTimeout:
        logger.warning("LLM warm-up timed out, tests may be slow on first request.")


@pytest.fixture(scope="session")
def client(base_url, _check_server_reachable, _warmup_llm):
    """httpx client scoped to the entire test session."""
    with httpx.Client(base_url=base_url, timeout=120.0) as c:
        yield c

