from __future__ import annotations

import pytest

from aruba_aos_switch import AosSwitchClient

FAKE_HOST = "switch.example.test"
FAKE_COOKIE = "fake-session-cookie-123"


@pytest.fixture
def client() -> AosSwitchClient:
    return AosSwitchClient(FAKE_HOST, "admin", "secret", verify_ssl=False)


@pytest.fixture
def mock_login(requests_mock):
    """Simule un login-sessions réussi (HTTP 201 + cookie)."""
    requests_mock.post(
        f"https://{FAKE_HOST}/rest/v1/login-sessions",
        status_code=201,
        json={"cookie": FAKE_COOKIE},
    )
    return requests_mock


@pytest.fixture
def logged_in_client(client: AosSwitchClient, mock_login) -> AosSwitchClient:
    client.login()
    return client
