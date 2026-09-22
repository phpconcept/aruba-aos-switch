# ARCHITECTURE.md — aruba-aos-switch

Ce document consigne le contexte, les choix d'architecture faits (et
pourquoi), et une liste vivante de points ouverts / idées pour plus tard.
Écrit au fil de l'eau, à corriger au fur et à mesure que les tests sur le
switch de labo confirment ou infirment certaines hypothèses.

Cette lib est consommée par [`aruba-dhcp-mgr`](https://github.com/phpconcept/aruba-dhcp-mgr)
(frontal web de gestion DHCP), dont les tests sur switch réel ont permis de
valider plusieurs points listés ci-dessous — voir son propre
`ARCHITECTURE.md` pour le détail des observations côté switch (comportement
d'écrasement sur nom de pool dupliqué, pools sans réseau, etc.).

## Contexte

Vincent a une classe PHP existante, `AapiAosSwitch`
(`aapi_aos_switch.class.php`, licence GPL, phpconcept), qui gère déjà :
connexion/authentification par session (cookie), les deux primitives
génériques de l'API REST ArubaOS-Switch (`any_cli` pour exécuter une
commande CLI unique, `batch_cli` pour en soumettre plusieurs d'un coup), et
des fonctions de gestion du serveur DHCP (pools et réservations/bindings)
bâties au-dessus de `any_cli`.

Objectif de ce projet : une librairie **Python** équivalente, pensée pour
être réutilisée par d'autres projets futurs (pas seulement DHCP — d'autres
domaines viendront ensuite), développée et versionnée sur **mowgli**
(serveur Debian de Vincent, accessible en SSH), avec publication éventuelle
sur GitHub (`phpconcept`), comme les projets `aruba-iot2mqtt`,
`aruba-mqtt-gateway` et `aruba-central-bridge` déjà présents sur mowgli
dans `/var/dev/`.

## Décisions prises

### Nom, licence, structure — alignés sur les projets mowgli existants

- Dépôt : `aruba-aos-switch`, dans `/var/dev/aruba-aos-switch` sur mowgli.
- Licence **MIT**, copyright phpconcept 2026 — comme `aruba-iot2mqtt` et
  `aruba-mqtt-gateway`, malgré l'en-tête GPL de la classe PHP d'origine
  (celle-ci n'est pas réutilisée telle quelle, juste comme référence de
  comportement).
- Documentation (`README.md`, ce fichier) en français, working document
  pour `DESIGN.md` — même convention que les autres projets.

### Packaging : package pip installable (`pyproject.toml`)

Contrairement aux projets mowgli existants (services autonomes avec
`requirements.txt`, fichiers à plat), celui-ci est une **librairie**
destinée à être importée par d'autres projets (outil de gestion DHCP,
futur plugin Jeedom, etc.). D'où une structure `src/aruba_aos_switch/`
standard, installable via `pip install -e .` en dev ou
`pip install git+https://github.com/phpconcept/aruba-aos-switch` une fois
publiée. Build backend : `hatchling` (simple, pas de compilation).

### Périmètre v0.1 : socle générique + DHCP

- Connexion/authentification (login-sessions, cookie, renouvellement auto).
- `any_cli()` / `batch_cli()` génériques.
- Module `dhcp` : parité fonctionnelle avec la classe PHP (pools et
  bindings), plus `server_status()` (statut enable/disable du serveur DHCP,
  ajouté après coup pour un besoin de `aruba-dhcp-mgr` — n'existait pas
  dans la classe PHP d'origine, pas d'endpoint REST structuré connu pour
  ce statut, parse le texte de `show dhcp-server`).

D'autres domaines (VLANs, interfaces, système...) viendront ensuite, sous
forme de nouveaux modules suivant le même principe.

### Authentification : session par cookie uniquement

La classe PHP a des champs `access_token`/`refresh_token`/`client_id`/
`client_secret` jamais utilisés en pratique — probablement prévus pour une
variante OAuth2 des API AOS-S plus récentes, jamais finalisée. Pour cette
v0.1, on ne implémente que le flux confirmé et utilisé en prod côté PHP :
login-sessions + cookie. Si besoin d'OAuth2 plus tard, ce sera un mode
d'authentification alternatif à ajouter à `AosSwitchClient`, pas une
réécriture.

### Style de code : fonctions plutôt que classes, sauf pour la connexion

`AosSwitchClient` est la seule classe avec état (session HTTP, cookie,
timestamp de connexion) — la connexion à un switch est un cas naturel de
gestion d'état. Les fonctions de domaine (`dhcp.py`) sont des fonctions
simples prenant un `AosSwitchClient` déjà connecté en premier argument
(`dhcp.pool_list(sw)` plutôt que `sw.dhcp.pool_list()`) : plus proche du
style « peu de classes » déjà utilisé dans `aruba-iot2mqtt`/
`aruba-mqtt-gateway`, et plus simple à tester (pas de couche
d'indirection supplémentaire).

### Modèles de données : dataclasses

`DhcpPool`, `DhcpBinding`, `IpRange` en `dataclasses` plutôt que des dicts
bruts (contrairement au PHP, qui manipule des tableaux associatifs) —
autocomplétion, typage, moins d'erreurs de clé.

### Gestion d'erreurs : exceptions typées plutôt que des tuples `[status, résultat]`

La classe PHP retourne systématiquement `[0|1, résultat]`. En Python, on
préfère des exceptions dédiées (`AuthenticationError`, `ApiError`,
`CliCommandError`, `ConnectionError`), plus idiomatique et qui évite
d'oublier de vérifier un code de retour.

### `dhcp.pool_edit` : fail-fast plutôt que best-effort

La version PHP de `dhcp_pool_edit` tente d'appliquer toutes les
sous-commandes même si certaines échouent, puis agrège les messages
d'erreur (`$v_global_status`/`$v_global_result`). La version Python
s'arrête à la première commande en échec (une exception interrompt la
fonction). Choix délibéré de simplicité — à revoir si un usage réel montre
que le comportement best-effort est nécessaire.

### `_dhcp_reconfigure` : contexte manager pour factoriser disable/enable

Chaque fonction d'écriture DHCP de la classe PHP répète le même motif :
désactiver le serveur DHCP, appliquer la modification, le réactiver (même
en cas d'échec de la modification). Factorisé en un context manager
(`contextlib.contextmanager`) dans `dhcp.py`, avec un `finally` qui
garantit la réactivation même si le corps lève une exception (l'échec de
réactivation est alors journalisé en warning, sans masquer l'erreur
d'origine).

## Recherches faites sur l'API (avant de coder le client bas niveau)

La doc HPE en ligne (`arubanetworking.hpe.com/techdocs/AOS-S/...`) est un
portail JS qui ne se laisse pas bien extraire par simple fetch, et le PDF
"Aruba REST API Guide" (16.11) ne détaille ni `/cli` ni `/cli_batch` en
profondeur. Deux sources plus utiles trouvées :

- Le dépôt officiel **[aruba/arubaos-switch-api-python](https://github.com/aruba/arubaos-switch-api-python)**
  (scripts d'exemple HPE) :
  - `loginOS.py` confirme le flux login/logout : `POST .../login-sessions`
    avec `{"user": ..., "password": ...}` (le nôtre reprend `userName`,
    comme dans la classe PHP — HTTP 201 attendu, cookie dans
    `response.json()['cookie']`) ; **logout = `DELETE .../login-sessions`**
    avec le cookie en header, HTTP 204 attendu. C'est ce comportement qui
    est implémenté dans `AosSwitchClient.logout()`.
  - `common.py`, fonction `batchcli()` : confirme le flux d'initiation
    (POST `cli_batch` en base64) — **et confirme aussi que même le script
    officiel HPE ne va pas plus loin** : il ne récupère pas le résultat du
    batch, seulement le code HTTP de soumission. Voir point ouvert
    ci-dessous.

## Points ouverts / à valider sur le switch de labo

Vincent a un switch ArubaOS-Switch réel accessible pour tester — ces
points sont à vérifier en priorité avant de considérer le module DHCP
"fiable" :

1. **Récupération du résultat de `batch_cli`.** Ni la classe PHP
   d'origine, ni le script officiel HPE (`arubaos-switch-api-python`), ni
   la doc PDF consultée n'indiquent comment récupérer le résultat d'un
   batch après le `CBS_INITIATED` initial. Piste à explorer sur le switch
   réel : interroger `/rest/v3` (ou la version disponible) pour voir si le
   switch expose un catalogue d'URIs (Swagger/OpenAPI ou liste de routes)
   qui révélerait un endpoint de statut/résultat — plus fiable que la doc
   publique, qui varie selon les versions de firmware. En attendant,
   `batch_cli()` ne fait que soumettre (pas de valeur de retour
   exploitable) ; pour un résultat par commande, utiliser `any_cli()` en
   boucle. **Toujours ouvert** — `aruba-dhcp-mgr` n'a pour l'instant utilisé
   que `any_cli()`/les fonctions `dhcp.*`, jamais `batch_cli()` directement.
2. ✅ **Format de `show dhcp-server binding` : validé.** `pool_list()` et
   `binding_list()` fonctionnent correctement sur switch réel (confirmé via
   `aruba-dhcp-mgr` : lecture, ajout, suppression de pools et réservations
   opérationnels). Nuance qui reste à surveiller : le cas d'un pool sans
   `network`/`mask` ni `static-bind` (ex. `testrr` dans les tests
   Vincent) — actuellement exclu silencieusement de `pool_list()`, voir
   `aruba-dhcp-mgr`/ARCHITECTURE.md §9.1.
3. **`session_timeout`.** Fixé à 600s par défaut (valeur reprise de la
   classe PHP) plutôt que lu depuis la configuration réelle du switch
   (`show session-timeout` ou équivalent). À revoir si besoin d'un
   comportement plus robuste (ex: lire la vraie valeur au login). Toujours
   ouvert.
4. ✅ **Scheme HTTP vs HTTPS : validé, HTTPS fonctionne.** Nécessite côté
   switch un certificat + `web-management ssl` + `rest-interface` activés
   (pas actifs par défaut) — voir `aruba-dhcp-mgr`/ARCHITECTURE.md §7 pour
   les commandes CLI exactes. Une fois ça fait, `https://` +
   `verify_ssl=False` (certificat auto-signé) fonctionne comme prévu ;
   `scheme="http"` reste disponible si besoin mais pas nécessaire en
   pratique.

## Idées / roadmap (non priorisé)

- Modules `vlan.py`, `interfaces.py`, `system.py`... suivant le même
  principe fonctionnel que `dhcp.py`.
- Éventuellement un mode d'authentification OAuth2 (client_id/secret) si
  besoin sur des switchs plus récents.
- CLI en ligne de commande (`aruba-aos-switch show-system ...`) si l'usage
  s'y prête, une fois la librairie stabilisée.
- Publication sur GitHub (`phpconcept/aruba-aos-switch`) et éventuellement
  PyPI — le module DHCP est maintenant validé sur switch réel (via
  `aruba-dhcp-mgr`), ne manque plus que la création du remote et le push
  (fait pour `aruba-dhcp-mgr`, pas encore pour ce dépôt).
- CI GitHub Actions (lint + tests unitaires) au moment de la publication.
