"""
Tests d'intégration, exécutés contre un vrai switch ArubaOS-Switch.

Ignorés par défaut (`pytest` seul ne les lance pas si les variables
d'environnement ci-dessous ne sont pas définies). Pour les activer :

    export AOS_SWITCH_HOST=192.168.x.x
    export AOS_SWITCH_USERNAME=admin
    export AOS_SWITCH_PASSWORD=...
    export AOS_SWITCH_VERIFY_SSL=false   # optionnel, "true"/"false"
    pytest -m integration

Volontairement limités à des opérations de LECTURE seule (login, any_cli en
lecture, pool_list, binding_list) : pas de création/suppression de pools ou
de bindings ici, pour ne jamais modifier la configuration d'un switch réel
sans validation explicite. Les scénarios d'écriture (pool_add/edit/delete,
binding_add/delete, et surtout la vérification du comportement de
batch_cli — voir DESIGN.md §"Points ouverts") sont à valider manuellement,
idéalement sur un switch de labo dédié.
"""

from __future__ import annotations

import os

import pytest

from aruba_aos_switch import AosSwitchClient
from aruba_aos_switch import dhcp

pytestmark = pytest.mark.integration


def _switch_config() -> dict | None:
    host = os.environ.get("AOS_SWITCH_HOST")
    if not host:
        return None
    return {
        "host": host,
        "username": os.environ.get("AOS_SWITCH_USERNAME", "admin"),
        "password": os.environ.get("AOS_SWITCH_PASSWORD", ""),
        "verify_ssl": os.environ.get("AOS_SWITCH_VERIFY_SSL", "false").lower()
        == "true",
    }


@pytest.fixture
def real_client():
    config = _switch_config()
    if config is None:
        pytest.skip(
            "AOS_SWITCH_HOST non défini : tests d'intégration désactivés "
            "(voir docstring de ce fichier)."
        )
    with AosSwitchClient(**config) as sw:
        yield sw


def test_login_and_any_cli(real_client: AosSwitchClient):
    output = real_client.any_cli("show system")
    assert output.strip() != ""


def test_dhcp_pool_list(real_client: AosSwitchClient):
    pools = dhcp.pool_list(real_client)
    assert isinstance(pools, list)


def test_dhcp_binding_list(real_client: AosSwitchClient):
    bindings = dhcp.binding_list(real_client)
    assert isinstance(bindings, list)
