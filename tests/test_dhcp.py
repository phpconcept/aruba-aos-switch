from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from aruba_aos_switch import AosSwitchClient
from aruba_aos_switch import dhcp

from .conftest import FAKE_HOST

FIXTURES_DIR = Path(__file__).parent / "fixtures"

# Exemple construit de sortie `show dhcp-server binding` : un pool dédié à
# une réservation statique (PRINTER-1, sans réseau propre - voir
# dhcp.binding_add), un bail dynamique dans un pool réseau normal (VLAN-31),
# et une deuxième réservation statique (CAMERA-2). Voir dhcp._parse_binding_line
# pour le détail du format attendu (à confirmer sur switch réel).
SAMPLE_BINDING_OUTPUT = """DHCP server is enabled

Pool : PRINTER-1
  192.168.31.10   AABBCC-DDEEFF   Infinite   Manual
Pool : VLAN-31
  192.168.31.55   112233-445566   Aug 20 2026 10:15:22   Dynamic
Pool : CAMERA-2
  192.168.32.20   665544-332211   Infinite   Manual
"""


def _mock_any_cli_output(requests_mock, text: str) -> None:
    encoded = base64.b64encode(text.encode()).decode()
    requests_mock.post(
        f"https://{FAKE_HOST}/rest/v3/cli",
        status_code=200,
        json={
            "status": "CCS_SUCCESS",
            "error_msg": "",
            "result_base64_encoded": encoded,
        },
    )


def _mock_any_cli_ok(requests_mock) -> None:
    _mock_any_cli_output(requests_mock, "")


# ----------------------------------------------------------------------
# pool_list (endpoint REST structuré)
# ----------------------------------------------------------------------


def test_pool_list(logged_in_client: AosSwitchClient, requests_mock):
    sample = json.loads((FIXTURES_DIR / "dhcp_pools_sample.json").read_text())
    requests_mock.get(
        f"https://{FAKE_HOST}/rest/v1/dhcp-server/pools",
        status_code=200,
        json=sample,
    )

    pools = dhcp.pool_list(logged_in_client)

    # Le 3e pool de l'échantillon (VLAN-31-b) n'a pas de réseau défini
    # (static-bind uniquement) : il doit être exclu.
    assert [p.name for p in pools] == ["VLAN-31", "VLAN-32"]

    vlan31 = pools[0]
    assert vlan31.ip == "192.168.31.0"
    assert vlan31.mask == "255.255.255.0"
    assert vlan31.default_gateways == ["192.168.31.1"]
    assert vlan31.dns_servers == ["8.8.8.8"]
    assert vlan31.ip_ranges[0].ip_start == "192.168.31.50"
    assert vlan31.ip_ranges[0].ip_end == "192.168.31.100"

    vlan32 = pools[1]
    assert vlan32.default_gateways == ["192.168.32.1", "192.168.32.2"]


# ----------------------------------------------------------------------
# binding_list (parsing texte via any_cli)
# ----------------------------------------------------------------------


def test_binding_list_all(logged_in_client: AosSwitchClient, requests_mock):
    _mock_any_cli_output(requests_mock, SAMPLE_BINDING_OUTPUT)

    bindings = dhcp.binding_list(logged_in_client)

    assert len(bindings) == 3
    static_bindings = [b for b in bindings if b.type == "static"]
    dynamic_bindings = [b for b in bindings if b.type == "dynamic"]
    assert {b.name for b in static_bindings} == {"PRINTER-1", "CAMERA-2"}
    assert dynamic_bindings[0].pool == "VLAN-31"
    assert dynamic_bindings[0].mac == "11:22:33:44:55:66"
    assert dynamic_bindings[0].ip == "192.168.31.55"
    assert dynamic_bindings[0].server_ip == FAKE_HOST


def test_binding_list_filter_static(logged_in_client: AosSwitchClient, requests_mock):
    _mock_any_cli_output(requests_mock, SAMPLE_BINDING_OUTPUT)
    bindings = dhcp.binding_list(logged_in_client, "static")
    assert len(bindings) == 2
    assert all(b.type == "static" for b in bindings)


def test_binding_list_dhcp_disabled(logged_in_client: AosSwitchClient, requests_mock):
    _mock_any_cli_output(requests_mock, "DHCP server is not enabled.")
    assert dhcp.binding_list(logged_in_client) == []


# ----------------------------------------------------------------------
# Écritures : vérifie la séquence disable / commande(s) / enable
# ----------------------------------------------------------------------


def test_pool_add_command_sequence(logged_in_client: AosSwitchClient, requests_mock):
    _mock_any_cli_ok(requests_mock)

    dhcp.pool_add(
        logged_in_client,
        "VLAN-40",
        "192.168.40.0",
        "255.255.255.0",
        dns_servers=["8.8.8.8"],
        default_gateways=["192.168.40.1"],
    )

    sent_cmds = [
        req.json()["cmd"]
        for req in requests_mock.request_history
        if req.path == "/rest/v3/cli"
    ]
    assert sent_cmds == [
        "dhcp-server disable",
        "dhcp-server pool 'VLAN-40' network 192.168.40.0 255.255.255.0",
        "dhcp-server pool 'VLAN-40' dns-server '8.8.8.8'",
        "dhcp-server pool 'VLAN-40' default-router '192.168.40.1'",
        "dhcp-server enable",
    ]


def test_pool_delete_reenables_server_even_on_failure(
    logged_in_client: AosSwitchClient, requests_mock
):
    def responder(request, context):
        cmd = request.json()["cmd"]
        context.status_code = 200
        if cmd == "no dhcp-server pool 'GHOST'":
            return {"status": "CCS_FAILURE", "error_msg": "no such pool"}
        return {
            "status": "CCS_SUCCESS",
            "error_msg": "",
            "result_base64_encoded": base64.b64encode(b"").decode(),
        }

    requests_mock.post(f"https://{FAKE_HOST}/rest/v3/cli", json=responder)

    with pytest.raises(Exception):
        dhcp.pool_delete(logged_in_client, "GHOST")

    sent_cmds = [
        req.json()["cmd"]
        for req in requests_mock.request_history
        if req.path == "/rest/v3/cli"
    ]
    # Le serveur DHCP doit être réactivé même si la suppression a échoué.
    assert sent_cmds == [
        "dhcp-server disable",
        "no dhcp-server pool 'GHOST'",
        "dhcp-server enable",
    ]


def test_binding_add_and_delete(logged_in_client: AosSwitchClient, requests_mock):
    _mock_any_cli_ok(requests_mock)

    dhcp.binding_add(
        logged_in_client, "PRINTER-2", "AA:BB:CC:DD:EE:FF", "192.168.31.20", "255.255.255.0"
    )
    dhcp.binding_delete(logged_in_client, "PRINTER-2")

    sent_cmds = [
        req.json()["cmd"]
        for req in requests_mock.request_history
        if req.path == "/rest/v3/cli"
    ]
    assert sent_cmds == [
        "dhcp-server disable",
        "dhcp-server pool 'PRINTER-2' static-bind ip 192.168.31.20 255.255.255.0 mac AA:BB:CC:DD:EE:FF",
        "dhcp-server enable",
        "dhcp-server disable",
        "no dhcp-server pool 'PRINTER-2'",
        "dhcp-server enable",
    ]
