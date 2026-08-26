"""Modèles de données pour les objets métier exposés par la librairie."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class IpRange:
    """Une plage d'adresses IP (utilisée dans un pool DHCP)."""

    ip_start: str
    ip_end: str


@dataclass
class DhcpPool:
    """Un pool DHCP tel que renvoyé par /rest/v1/dhcp-server/pools."""

    name: str
    ip: str | None = None
    mask: str | None = None
    default_gateways: list[str] = field(default_factory=list)
    dns_servers: list[str] = field(default_factory=list)
    ip_ranges: list[IpRange] = field(default_factory=list)


@dataclass
class DhcpBinding:
    """
    Une réservation DHCP (statique ou bail dynamique en cours), telle que
    parsée depuis la sortie de `show dhcp-server binding`.
    """

    ip: str
    mac: str
    type: str  # "static" ou "dynamic"
    expire: str
    server_ip: str
    name: str = ""
    pool: str = ""
