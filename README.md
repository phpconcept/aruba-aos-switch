# aruba-aos-switch

Client Python pour l'API REST des switchs **HPE Aruba Networking** tournant
**ArubaOS-Switch (AOS-S)**.

Fournit une connexion authentifiée (session par cookie), les deux
primitives génériques de l'API (`any_cli` / `batch_cli` — exécuter
n'importe quelle commande CLI via l'API REST), et des fonctions de plus
haut niveau par domaine, à commencer par la gestion du serveur DHCP
(pools et réservations).

> **Statut : v0.1, module DHCP validé sur switch réel.** Portage/réécriture
> en Python d'une classe PHP existante
> ([`AapiAosSwitch`](ARCHITECTURE.md#contexte)) qui gérait déjà
> l'authentification et le DHCP. Utilisée en pratique par
> [`aruba-dhcp-mgr`](https://github.com/phpconcept/aruba-dhcp-mgr), qui a
> permis de valider connexion HTTPS, lecture/écriture des pools et
> réservations sur un switch réel. Voir `ARCHITECTURE.md` pour l'état
> d'avancement, les choix faits et les points encore ouverts.

## Installation

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Utilisation

```python
from aruba_aos_switch import AosSwitchClient
from aruba_aos_switch import dhcp

with AosSwitchClient("192.168.1.1", "admin", "motdepasse") as sw:
    # Commande CLI arbitraire
    print(sw.any_cli("show system"))

    # Gestion DHCP
    pools = dhcp.pool_list(sw)
    dhcp.pool_add(sw, "VLAN-40", "192.168.40.0", "255.255.255.0",
                  dns_servers=["8.8.8.8"],
                  default_gateways=["192.168.40.1"],
                  domain_name="lan.local",
                  lease="1:00:00")  # ou "infinite"
    bindings = dhcp.binding_list(sw, "static")

    is_enabled = dhcp.server_status(sw)  # True/False, ou None si indéterminé
    dhcp.server_disable(sw)
    dhcp.server_enable(sw)

    # domain-name n'est exposé ni par le REST ni par `show dhcp-server pool
    # <name>` : lecture dédiée via `show running-config` (plus coûteuse,
    # à réserver à un usage ponctuel — voir ARCHITECTURE.md).
    domain = dhcp.pool_domain_name(sw, "VLAN-40")
```

Par défaut, la connexion se fait en HTTPS avec vérification du certificat
TLS **désactivée** (`verify_ssl=False`), car les switchs utilisent en
général un certificat auto-signé. Passer `verify_ssl=True` (et
éventuellement `scheme="http"` si l'API REST n'est pas exposée en HTTPS sur
votre switch) selon votre configuration.

Côté switch, HTTPS et l'API REST ne sont pas actifs par défaut — un
certificat, `web-management ssl` et `rest-interface` doivent être activés
en CLI au préalable (voir `ARCHITECTURE.md`, point 4).

## Périmètre actuel

- Connexion / authentification par session (login-sessions), avec
  renouvellement automatique.
- `any_cli(cmd)` : exécute une commande CLI, retourne sa sortie texte.
- `batch_cli(cmds)` : soumet un lot de commandes CLI (voir limitation
  connue dans `ARCHITECTURE.md`).
- Module `dhcp` : liste/ajout/modification/suppression de pools DHCP
  (réseau/passerelles/DNS/domain-name/lease/plages), liste/ajout/
  suppression de réservations (bindings) statiques, `server_status()`/
  `server_enable()`/`server_disable()` (statut et bascule du serveur DHCP),
  `pool_domain_name()` (lecture dédiée via `show running-config`).

D'autres domaines (VLANs, interfaces, système...) pourront être ajoutés
sous forme de nouveaux modules (`vlan.py`, `interfaces.py`...) suivant le
même principe : des fonctions prenant un `AosSwitchClient` en premier
argument.

## Tests

```sh
pytest                    # tests unitaires (API mockée, aucun switch requis)
pytest -m integration     # tests d'intégration contre un switch réel (voir tests/test_integration.py)
```

## Documentation de référence

- [Aruba REST API Guide for ArubaOS-Switch](https://arubanetworking.hpe.com/techdocs/AOS-S/16.10/RESTAPI/content/rest%20api.htm)
- [aruba/arubaos-switch-api-python](https://github.com/aruba/arubaos-switch-api-python) — scripts d'exemple officiels HPE, utilisés pour confirmer certains comportements (login/logout notamment) non détaillés dans la doc publique.
- *2930F/2930M Management and Configuration Guide for AOS-S 16.11*, chapitre 8 (DHCPv4 server) — référence pour la syntaxe CLI du module `dhcp` (commandes `dhcp-server pool`, limites — ex. 8 passerelles/DNS max — pools authoritative, dummy pools...).

## Licence

MIT, voir [LICENSE](LICENSE).
