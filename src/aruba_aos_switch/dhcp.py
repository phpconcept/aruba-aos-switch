"""
Fonctions de gestion du serveur DHCP d'un switch ArubaOS-Switch.

Portage/réécriture de la logique DHCP de la classe PHP `AapiAosSwitch`
d'origine (voir DESIGN.md pour le détail des choix). Style volontairement
fonctionnel plutôt qu'orienté objet : chaque fonction prend un
`AosSwitchClient` déjà connecté en premier argument, à l'image de
`aim_decoder.py`/`aim_sinks.py` dans les autres projets Python de ce dépôt.

`pool_list()` s'appuie sur l'endpoint REST structuré
`/rest/v1/dhcp-server/pools` (JSON propre). `binding_list()` n'a pas
d'équivalent REST structuré connu : il repose sur le parsing du texte
renvoyé par `show dhcp-server binding` via any_cli(), comme dans la classe
PHP d'origine — donc potentiellement sensible au formatage exact de cette
sortie selon la version de firmware (à valider sur switch réel).
"""

from __future__ import annotations

import logging
import re
from contextlib import contextmanager
from typing import Iterator

from .client import AosSwitchClient
from .exceptions import CliCommandError
from .models import DhcpBinding, DhcpPool, IpRange

logger = logging.getLogger("aruba_aos_switch")


# ----------------------------------------------------------------------
# État global du serveur DHCP
# ----------------------------------------------------------------------

# Sortie réelle de `show dhcp-server` (ArubaOS-Switch, relevée sur switch) :
#
#  Configuration and Status - DHCP Server
#
#   DHCP Server Enabled       : Yes
#   DHCPv4 Operational Status : Enabled
#   Traps Enabled             : Yes
#   Persistent Lease Database : No
#   Conflict Logging Enabled  : No
#   DHCP VLAN Interfaces      : 31,32
#
# On s'appuie sur « DHCP Server Enabled » (Yes/No) : c'est le champ qui
# correspond directement aux commandes `dhcp-server enable`/`disable`
# utilisées ailleurs dans ce module. « DHCPv4 Operational Status » et
# « DHCP VLAN Interfaces » sont aussi dans cette sortie mais pas exploités
# pour l'instant (voir ARCHITECTURE.md du projet aruba-dhcp-mgr, §9.5).
_SERVER_STATUS_RE = re.compile(r"DHCP Server Enabled\s*:\s*(Yes|No)", re.IGNORECASE)


def server_status(client: AosSwitchClient) -> bool | None:
    """
    Indique si le serveur DHCP est actuellement activé sur le switch
    (champ « DHCP Server Enabled » de `show dhcp-server`).

    Pas d'endpoint REST structuré connu pour ce statut : repose sur le texte
    de `show dhcp-server` (any_cli), avec la même réserve que
    binding_list() sur la sensibilité au firmware. Si le motif ne
    correspond pas à la sortie observée, on renvoie None (statut
    indéterminé) plutôt que de risquer un faux Enabled/Disabled.
    """
    output = client.any_cli("show dhcp-server")
    match = _SERVER_STATUS_RE.search(output)
    if match is None:
        logger.warning(
            "Impossible de déterminer l'état du serveur DHCP depuis la sortie "
            "de 'show dhcp-server' (motif non trouvé) — à valider sur ce firmware."
        )
        return None
    return match.group(1).lower() == "yes"


def server_enable(client: AosSwitchClient) -> None:
    """Active le serveur DHCP (`dhcp-server enable`).

    À distinguer de `_dhcp_reconfigure()` : ici, c'est l'action demandée
    explicitement par l'appelant, pas une réactivation automatique après
    une modification de pool/réservation.
    """
    client.any_cli("dhcp-server enable")


def server_disable(client: AosSwitchClient) -> None:
    """Désactive le serveur DHCP (`dhcp-server disable`).

    Action impactante : coupe la distribution DHCP sur tous les VLANs
    concernés par ce switch. À l'appelant de s'assurer que l'utilisateur en
    a bien conscience (confirmation côté UI, par exemple).
    """
    client.any_cli("dhcp-server disable")


# ----------------------------------------------------------------------
# Pools DHCP
# ----------------------------------------------------------------------


def pool_list(client: AosSwitchClient) -> list[DhcpPool]:
    """
    Liste les pools DHCP configurés (GET /rest/v1/dhcp-server/pools).

    Les pools sans réseau/masque définis (utilisés par le switch pour
    porter uniquement des réservations statiques, voir `binding_add`) sont
    exclus de cette liste, comme dans la classe PHP d'origine.
    """
    data = client.request_json("GET", "dhcp-server/pools", expected_status=(200,))
    raw_list = data.get("dhcp_server_pool_element")
    if raw_list is None:
        raise CliCommandError(
            "Champ 'dhcp_server_pool_element' absent de la réponse du switch"
        )

    pools: list[DhcpPool] = []
    for item in raw_list:
        network_ip = (item.get("network_ip") or {}).get("octets")
        network_mask = (item.get("network_mask") or {}).get("octets")
        if network_ip is None or network_mask is None:
            continue

        ip_ranges = [
            IpRange(r["ip_start"]["octets"], r["ip_end"]["octets"])
            for r in item.get("ip_range", [])
            if r.get("ip_start") and r.get("ip_end")
        ]

        pools.append(
            DhcpPool(
                name=item["pool_name"],
                ip=network_ip,
                mask=network_mask,
                default_gateways=[
                    r["octets"] for r in item.get("default_routers", [])
                ],
                dns_servers=[r["octets"] for r in item.get("dns_servers", [])],
                ip_ranges=ip_ranges,
            )
        )
    return pools


def pool_add(
    client: AosSwitchClient,
    name: str,
    ip: str,
    mask: str,
    *,
    dns_servers: list[str] | None = None,
    default_gateways: list[str] | None = None,
    server_enable: bool = True,
) -> None:
    """Crée un pool DHCP réseau (`dhcp-server pool ... network ...`)."""
    with _dhcp_reconfigure(client, server_enable):
        client.any_cli(f"dhcp-server pool '{name}' network {ip} {mask}")
        if dns_servers is not None:
            _set_pool_dns_servers(client, name, dns_servers)
        if default_gateways is not None:
            _set_pool_default_gateways(client, name, default_gateways)


def pool_edit(
    client: AosSwitchClient,
    name: str,
    *,
    ip: str | None = None,
    mask: str | None = None,
    dns_servers: list[str] | None = None,
    default_gateways: list[str] | None = None,
    ip_ranges_add: list[IpRange] | None = None,
    ip_ranges_remove: list[IpRange] | None = None,
    server_enable: bool = True,
) -> None:
    """
    Modifie un pool DHCP existant. Seuls les paramètres fournis (non None)
    sont modifiés.

    Contrairement à la classe PHP d'origine (qui tentait d'appliquer toutes
    les sous-commandes même en cas d'échec partiel, puis agrégeait les
    erreurs), cette version s'arrête à la première commande en échec
    (fail-fast) — plus simple à raisonner côté appelant. Voir DESIGN.md.
    """
    with _dhcp_reconfigure(client, server_enable):
        if ip is not None and mask is not None:
            client.any_cli(f"dhcp-server pool '{name}' network {ip} {mask}")
        if dns_servers is not None:
            _set_pool_dns_servers(client, name, dns_servers)
        if default_gateways is not None:
            _set_pool_default_gateways(client, name, default_gateways)
        for ip_range in ip_ranges_add or []:
            client.any_cli(
                f"dhcp-server pool '{name}' range {ip_range.ip_start} {ip_range.ip_end}"
            )
        for ip_range in ip_ranges_remove or []:
            client.any_cli(
                f"no dhcp-server pool '{name}' range {ip_range.ip_start} {ip_range.ip_end}"
            )


def pool_delete(
    client: AosSwitchClient, name: str, *, server_enable: bool = True
) -> None:
    """Supprime un pool DHCP (`no dhcp-server pool ...`)."""
    with _dhcp_reconfigure(client, server_enable):
        client.any_cli(f"no dhcp-server pool '{name}'")


def _set_pool_dns_servers(
    client: AosSwitchClient, name: str, dns_servers: list[str]
) -> None:
    if not dns_servers:
        client.any_cli(f"no dhcp-server pool '{name}' dns-server")
    else:
        client.any_cli(f"dhcp-server pool '{name}' dns-server '{','.join(dns_servers)}'")


def _set_pool_default_gateways(
    client: AosSwitchClient, name: str, default_gateways: list[str]
) -> None:
    if not default_gateways:
        client.any_cli(f"no dhcp-server pool '{name}' default-router")
    else:
        client.any_cli(
            f"dhcp-server pool '{name}' default-router '{','.join(default_gateways)}'"
        )


# ----------------------------------------------------------------------
# Réservations (bindings) DHCP
# ----------------------------------------------------------------------


def binding_list(
    client: AosSwitchClient, binding_type: str = "all"
) -> list[DhcpBinding]:
    """
    Liste les réservations DHCP (statiques et/ou baux dynamiques en cours)
    en parsant la sortie de `show dhcp-server binding`.

    binding_type : "all" (défaut), "dynamic" ou "static".
    """
    if binding_type not in ("all", "dynamic", "static"):
        binding_type = "all"

    output = client.any_cli("show dhcp-server binding")
    if output.strip() == "DHCP server is not enabled.":
        return []

    result: list[DhcpBinding] = []
    pool_name = ""
    for line in output.splitlines():
        pool_match = _POOL_HEADER_RE.match(line)
        if pool_match:
            pool_name = pool_match.group(1).strip()
            continue

        if not _IP_LEADING_RE.match(line):
            continue

        binding = _parse_binding_line(f"{line} {pool_name}", server_ip=client.host)
        if binding is None:
            continue
        if binding_type in ("all", binding.type):
            result.append(binding)

    return result


def binding_add(
    client: AosSwitchClient,
    name: str,
    mac: str,
    ip: str,
    ip_mask: str,
    *,
    server_enable: bool = True,
) -> None:
    """
    Ajoute une réservation DHCP statique. Sur ArubaOS-Switch, une
    réservation statique est modélisée comme un pool dédié portant un seul
    `static-bind` — même mécanisme que `pool_add`/`pool_delete` sous le
    capot, exposé séparément ici pour la clarté de l'intention côté appelant
    (comme dans la classe PHP d'origine).
    """
    with _dhcp_reconfigure(client, server_enable):
        client.any_cli(
            f"dhcp-server pool '{name}' static-bind ip {ip} {ip_mask} mac {mac}"
        )


def binding_delete(
    client: AosSwitchClient, name: str, *, server_enable: bool = True
) -> None:
    """Supprime une réservation DHCP statique (identique à pool_delete)."""
    with _dhcp_reconfigure(client, server_enable):
        client.any_cli(f"no dhcp-server pool '{name}'")


# ----------------------------------------------------------------------
# Helpers internes
# ----------------------------------------------------------------------


@contextmanager
def _dhcp_reconfigure(
    client: AosSwitchClient, server_enable: bool = True
) -> Iterator[None]:
    """
    Encadre une modification de configuration DHCP : désactive le serveur
    DHCP, exécute le bloc, puis le réactive (si `server_enable`) même en cas
    d'erreur dans le bloc. C'est le pattern répété dans chaque fonction
    d'écriture de la classe PHP d'origine, factorisé ici.

    Si la réactivation échoue, l'erreur est journalisée (warning) mais
    n'écrase pas une éventuelle exception levée dans le bloc.
    """
    try:
        client.any_cli("dhcp-server disable")
    except CliCommandError as exc:
        raise CliCommandError(
            f"Échec de la désactivation du serveur DHCP avant modification : {exc}"
        ) from exc

    try:
        yield
    finally:
        if server_enable:
            try:
                client.any_cli("dhcp-server enable")
            except CliCommandError as exc:
                logger.warning(
                    "Échec de la réactivation du serveur DHCP après modification : %s",
                    exc,
                )


_POOL_HEADER_RE = re.compile(r"^\s*Pool\s*:\s*(.*)$")
_IP_LEADING_RE = re.compile(r"^\s*(\d{1,3}\.){3}\d{1,3}")
_BINDING_LINE_RE = re.compile(
    r"^\s*(?P<ip>(?:\d{1,3}\.){3}\d{1,3})\s*"
    r"(?P<mac>\w{6}-\w{6})\s*"
    r"(?P<expire>\w{8}|[A-Za-z]{3} \d{2} \d{4} \d{2}:\d{2}:\d{2})\s*"
    r"(?P<kind>Manual|Dynamic)\s*"
    r"(?P<rest>.+)$"
)
_MAC_RE = re.compile(r"^[0-9A-F]{12}$")


def _parse_binding_line(line: str, *, server_ip: str) -> DhcpBinding | None:
    """
    Parse une ligne de `show dhcp-server binding` (à laquelle le nom du pool
    courant a été ajouté en suffixe par binding_list()).

    Reprend telle quelle la regex de la classe PHP d'origine
    (`_dhcp_binding_parse_line`) : à valider/ajuster sur un switch réel, le
    format exact de cette sortie CLI n'étant pas documenté dans l'API REST.
    """
    match = _BINDING_LINE_RE.match(line)
    if not match:
        return None

    kind = "static" if match.group("kind") == "Manual" else "dynamic"
    trailing = match.group("rest").strip()
    mac = _format_mac(match.group("mac")) or match.group("mac")

    binding = DhcpBinding(
        ip=match.group("ip"),
        mac=mac,
        type=kind,
        expire=match.group("expire"),
        server_ip=server_ip,
    )
    if kind == "static":
        binding.name = trailing
    else:
        binding.pool = trailing
    return binding


def _format_mac(mac: str) -> str | None:
    """'AABBCC-DDEEFF' -> 'AA:BB:CC:DD:EE:FF'. None si le format est invalide."""
    cleaned = mac.upper().replace("-", "").replace(":", "")
    if not _MAC_RE.match(cleaned):
        return None
    pairs = [cleaned[i : i + 2] for i in range(0, 12, 2)]
    return ":".join(pairs)
