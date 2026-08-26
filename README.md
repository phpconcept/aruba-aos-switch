# aruba-aos-switch

Client Python pour l'API REST des switchs **HPE Aruba Networking** tournant
**ArubaOS-Switch (AOS-S)**.

Fournit une connexion authentifiée (session par cookie), les deux
primitives génériques de l'API (`any_cli` / `batch_cli` — exécuter
n'importe quelle commande CLI via l'API REST), et des fonctions de plus
haut niveau par domaine, à commencer par la gestion du serveur DHCP
(pools et réservations).

> **Statut : démarrage (v0.1).** Portage/réécriture en Python d'une classe
> PHP existante ([`AapiAosSwitch`](DESIGN.md#contexte)) qui gérait déjà
> l'authentification et le DHCP. Voir `DESIGN.md` pour l'état d'avancement,
> les choix faits et les points encore à valider sur un switch réel.

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
                  default_gateways=["192.168.40.1"])
    bindings = dhcp.binding_list(sw, "static")
```

Par défaut, la connexion se fait en HTTPS avec vérification du certificat
TLS **désactivée** (`verify_ssl=False`), car les switchs utilisent en
général un certificat auto-signé. Passer `verify_ssl=True` (et
éventuellement `scheme="http"` si l'API REST n'est pas exposée en HTTPS sur
votre switch) selon votre configuration.

## Périmètre actuel

- Connexion / authentification par session (login-sessions), avec
  renouvellement automatique.
- `any_cli(cmd)` : exécute une commande CLI, retourne sa sortie texte.
- `batch_cli(cmds)` : soumet un lot de commandes CLI (voir limitation
  connue dans `DESIGN.md`).
- Module `dhcp` : liste/ajout/modification/suppression de pools DHCP,
  liste/ajout/suppression de réservations (bindings) statiques.

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

## Licence

MIT, voir [LICENSE](LICENSE).
