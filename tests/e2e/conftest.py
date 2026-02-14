import httpx
import pytest

BASE_URL = "http://localhost:8000"


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


@pytest.fixture(scope="session")
def client(base_url, _check_server_reachable):
    """httpx client scoped to the entire test session."""
    with httpx.Client(base_url=base_url, timeout=120.0) as c:
        yield c
