from __future__ import annotations

import base64
import time

import pytest

from aruba_aos_switch import (
    ApiError,
    AuthenticationError,
    CliCommandError,
)
from aruba_aos_switch.client import AosSwitchClient

from .conftest import FAKE_COOKIE, FAKE_HOST


def test_login_success(client: AosSwitchClient, mock_login):
    client.login()
    assert client.is_logged_in()
    sent_headers = mock_login.request_history[0].json()
    assert sent_headers == {"userName": "admin", "password": "secret"}


def test_login_refused(client: AosSwitchClient, requests_mock):
    requests_mock.post(
        f"https://{FAKE_HOST}/rest/v1/login-sessions",
        status_code=401,
        json={},
    )
    with pytest.raises(AuthenticationError):
        client.login()
    assert not client.is_logged_in()


def test_login_missing_cookie_field(client: AosSwitchClient, requests_mock):
    requests_mock.post(
        f"https://{FAKE_HOST}/rest/v1/login-sessions",
        status_code=201,
        json={},
    )
    with pytest.raises(AuthenticationError):
        client.login()


def test_login_if_needed_reuses_session(logged_in_client: AosSwitchClient, mock_login):
    call_count_before = mock_login.call_count
    logged_in_client.login_if_needed()
    assert mock_login.call_count == call_count_before  # pas de nouveau login


def test_login_if_needed_renews_expired_session(
    logged_in_client: AosSwitchClient, mock_login
):
    logged_in_client._session_ts = time.time() - logged_in_client.session_timeout - 1
    logged_in_client.login_if_needed()
    assert mock_login.call_count == 2


def test_logout_clears_session(logged_in_client: AosSwitchClient, requests_mock):
    requests_mock.delete(
        f"https://{FAKE_HOST}/rest/v1/login-sessions", status_code=204
    )
    logged_in_client.logout()
    assert not logged_in_client.is_logged_in()


def test_logout_failure_is_swallowed(logged_in_client: AosSwitchClient, requests_mock):
    requests_mock.delete(
        f"https://{FAKE_HOST}/rest/v1/login-sessions", status_code=500
    )
    logged_in_client.logout()  # ne doit pas lever
    assert not logged_in_client.is_logged_in()


def test_any_cli_success(logged_in_client: AosSwitchClient, requests_mock):
    encoded = base64.b64encode(b"System info here\n").decode()
    requests_mock.post(
        f"https://{FAKE_HOST}/rest/v3/cli",
        status_code=200,
        json={
            "status": "CCS_SUCCESS",
            "error_msg": "",
            "result_base64_encoded": encoded,
        },
    )
    output = logged_in_client.any_cli("show system")
    assert output == "System info here\n"
    assert requests_mock.request_history[-1].json() == {"cmd": "show system"}
    assert requests_mock.request_history[-1].headers["Cookie"] == FAKE_COOKIE


def test_any_cli_error_status(logged_in_client: AosSwitchClient, requests_mock):
    requests_mock.post(
        f"https://{FAKE_HOST}/rest/v3/cli",
        status_code=200,
        json={"status": "CCS_FAILURE", "error_msg": "Invalid input"},
    )
    with pytest.raises(CliCommandError):
        logged_in_client.any_cli("bogus command")


def test_any_cli_unexpected_http_code(logged_in_client: AosSwitchClient, requests_mock):
    requests_mock.post(f"https://{FAKE_HOST}/rest/v3/cli", status_code=500)
    with pytest.raises(ApiError):
        logged_in_client.any_cli("show system")


def test_batch_cli_success(logged_in_client: AosSwitchClient, requests_mock):
    requests_mock.post(
        f"https://{FAKE_HOST}/rest/v3/cli_batch",
        status_code=202,
        json={"status": "CBS_INITIATED"},
    )
    logged_in_client.batch_cli(["vlan 10", "name 'servers'"])
    sent = requests_mock.request_history[-1].json()
    decoded = base64.b64decode(sent["cli_batch_base64_encoded"]).decode()
    assert decoded == "vlan 10\nname 'servers'\n"


def test_batch_cli_rejected(logged_in_client: AosSwitchClient, requests_mock):
    requests_mock.post(
        f"https://{FAKE_HOST}/rest/v3/cli_batch",
        status_code=202,
        json={"status": "CBS_FAILURE", "error_msg": "bad batch"},
    )
    with pytest.raises(CliCommandError):
        logged_in_client.batch_cli(["bogus"])
