"""
Client bas niveau pour l'API REST des switchs HPE Aruba Networking sous
ArubaOS-Switch (AOS-S).

Gère la connexion (login/logout par cookie de session), le renouvellement
automatique de session, et les deux primitives génériques `any_cli()` /
`batch_cli()` qui permettent de piloter le switch avec n'importe quelle
commande CLI. Les modules de plus haut niveau (voir `dhcp.py`) sont bâtis
au-dessus de ce client.

Voir DESIGN.md pour le détail des choix (format du cookie, versions d'API
utilisées par endpoint, état de `batch_cli`...).
"""

from __future__ import annotations

import base64
import logging
import time

import requests

from .exceptions import (
    ApiError,
    AuthenticationError,
    CliCommandError,
)
from .exceptions import ConnectionError as AosConnectionError

logger = logging.getLogger("aruba_aos_switch")


class AosSwitchClient:
    """
    Connexion à un switch ArubaOS-Switch.

    Usage typique :

        with AosSwitchClient("10.0.0.1", "admin", "secret") as sw:
            status, output = sw.any_cli("show system")

    ou sans context manager :

        sw = AosSwitchClient("10.0.0.1", "admin", "secret")
        sw.login()
        ...
        sw.logout()
    """

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        *,
        scheme: str = "https",
        verify_ssl: bool = False,
        timeout: float = 10.0,
        session_timeout: float = 600.0,
        login_api_version: str = "v1",
        cli_api_version: str = "v3",
        rest_api_version: str = "v1",
    ) -> None:
        """
        Paramètres :
          host : adresse IP ou nom DNS du switch.
          username, password : identifiants d'authentification.
          scheme : "https" (recommandé) ou "http" selon la configuration du
            switch. La classe PHP d'origine utilisait "http" ; à ajuster
            selon l'environnement (voir DESIGN.md).
          verify_ssl : vérification du certificat TLS. False par défaut car
            les switchs utilisent en général un certificat auto-signé.
          timeout : timeout HTTP en secondes pour chaque requête.
          session_timeout : durée de vie supposée de la session côté switch
            (en secondes) ; passé ce délai depuis le dernier login, une
            reconnexion automatique est déclenchée avant la requête
            suivante. 600s = valeur par défaut historique reprise de la
            classe PHP ; le switch a sa propre configuration de timeout de
            session qui peut différer (non interrogée automatiquement pour
            l'instant, voir DESIGN.md).
          login_api_version : version d'API pour /login-sessions.
          cli_api_version : version d'API pour /cli et /cli_batch.
          rest_api_version : version d'API par défaut pour les autres
            endpoints REST structurés (ex: dhcp-server/pools).
        """
        self.host = host
        self.username = username
        self.password = password
        self.scheme = scheme
        self.verify_ssl = verify_ssl
        self.timeout = timeout
        self.session_timeout = session_timeout
        self.login_api_version = login_api_version
        self.cli_api_version = cli_api_version
        self.rest_api_version = rest_api_version

        self._session = requests.Session()
        self._session_cookie: str | None = None
        self._session_ts: float = 0.0

        if not verify_ssl:
            try:
                from urllib3.exceptions import InsecureRequestWarning

                requests.packages.urllib3.disable_warnings(InsecureRequestWarning)  # type: ignore[attr-defined]
            except Exception:  # pragma: no cover - best effort seulement
                pass

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> "AosSwitchClient":
        self.login()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.logout()

    # ------------------------------------------------------------------
    # Authentification
    # ------------------------------------------------------------------

    @property
    def base_url(self) -> str:
        return f"{self.scheme}://{self.host}"

    def is_logged_in(self) -> bool:
        return self._session_cookie is not None

    def login_if_needed(self) -> None:
        """Reconnecte si aucune session n'est établie, ou si elle a expiré."""
        if self._session_cookie is None:
            self.login()
            return
        if (self._session_ts + self.session_timeout) < time.time():
            logger.debug("Session expirée (>%ss), reconnexion", self.session_timeout)
            self.login()

    def login(self, username: str | None = None, password: str | None = None) -> None:
        """
        Ouvre une session sur le switch (POST /rest/{v}/login-sessions).

        Lève AuthenticationError si les identifiants sont refusés ou si la
        réponse est inattendue, ConnectionError si le switch est injoignable.
        """
        username = username or self.username
        password = password or self.password
        self.username, self.password = username, password

        self._session_cookie = None
        self._session_ts = 0.0

        response = self._request(
            "POST",
            "login-sessions",
            api_version=self.login_api_version,
            json_body={"userName": username, "password": password},
            authenticated=False,
        )

        if response.status_code != 201:
            raise AuthenticationError(
                f"Login refusé par {self.host} (HTTP {response.status_code})"
            )

        try:
            data = response.json()
        except ValueError as exc:
            raise AuthenticationError(
                "Réponse de login illisible (JSON invalide)"
            ) from exc

        cookie = data.get("cookie")
        if not cookie:
            raise AuthenticationError(
                "Champ 'cookie' absent de la réponse de login-sessions"
            )

        self._session_cookie = cookie
        self._session_ts = time.time()
        logger.info("Connecté à %s", self.host)

    def logout(self) -> None:
        """
        Ferme la session en cours (DELETE /rest/{v}/login-sessions).

        N'échoue jamais bruyamment : un logout qui échoue est journalisé en
        warning mais n'interrompt pas l'appelant (la session locale est de
        toute façon oubliée).
        """
        if self._session_cookie is None:
            return
        try:
            self._request(
                "DELETE", "login-sessions", api_version=self.login_api_version
            )
        except (ApiError, AosConnectionError) as exc:
            logger.warning("Échec du logout sur %s (ignoré) : %s", self.host, exc)
        finally:
            self._session_cookie = None
            self._session_ts = 0.0

    # ------------------------------------------------------------------
    # Requête HTTP générique
    # ------------------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        api_version: str,
        json_body: dict | None = None,
        authenticated: bool = True,
    ) -> requests.Response:
        """
        Effectue une requête HTTP vers /rest/{api_version}/{path}.

        Retourne la réponse `requests.Response` brute : c'est à l'appelant
        (login/any_cli/batch_cli/modules de plus haut niveau) d'interpréter
        le code HTTP et le corps selon la sémantique propre à chaque
        endpoint (les codes de succès attendus varient : 200, 201, 202...).
        """
        if authenticated:
            self.login_if_needed()

        url = f"{self.base_url}/rest/{api_version}/{path.lstrip('/')}"
        headers = {}
        if authenticated and self._session_cookie:
            headers["Cookie"] = self._session_cookie

        logger.debug("HTTP %s %s", method, url)
        try:
            return self._session.request(
                method,
                url,
                json=json_body,
                headers=headers,
                timeout=self.timeout,
                verify=self.verify_ssl,
            )
        except requests.RequestException as exc:
            raise AosConnectionError(
                f"Impossible de joindre {self.host} ({method} {path}) : {exc}"
            ) from exc

    def request_json(
        self,
        method: str,
        path: str,
        *,
        api_version: str | None = None,
        json_body: dict | None = None,
        expected_status: tuple[int, ...] = (200,),
    ) -> dict:
        """
        Helper pour les endpoints REST "structurés" (ex: dhcp-server/pools) :
        fait la requête, vérifie le code HTTP, retourne le JSON décodé.

        Lève ApiError si le code HTTP n'est pas dans `expected_status` ou si
        le corps n'est pas un JSON valide.
        """
        response = self._request(
            method,
            path,
            api_version=api_version or self.rest_api_version,
            json_body=json_body,
        )
        if response.status_code not in expected_status:
            raise ApiError(
                f"HTTP {response.status_code} inattendu pour {method} {path} "
                f"(attendu : {expected_status})",
                http_code=response.status_code,
            )
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError as exc:
            raise ApiError(
                f"Réponse illisible (JSON invalide) pour {method} {path}"
            ) from exc

    # ------------------------------------------------------------------
    # any_cli / batch_cli
    # ------------------------------------------------------------------

    def any_cli(self, cmd: str) -> str:
        """
        Exécute une commande CLI unique via POST /rest/{v}/cli et retourne
        sa sortie texte décodée.

        Lève CliCommandError si le switch renvoie un statut d'erreur
        (status != CCS_SUCCESS), ApiError pour toute autre anomalie de
        réponse (HTTP ou JSON).
        """
        response = self._request(
            "POST",
            "cli",
            api_version=self.cli_api_version,
            json_body={"cmd": cmd},
        )
        if response.status_code not in (200, 201, 202):
            raise ApiError(
                f"HTTP {response.status_code} inattendu pour any_cli('{cmd}')",
                http_code=response.status_code,
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise ApiError("Réponse any_cli illisible (JSON invalide)") from exc

        status = data.get("status")
        if status != "CCS_SUCCESS":
            raise CliCommandError(
                f"Erreur API ({status}) pour '{cmd}' : {data.get('error_msg')}",
                api_status=status,
            )

        encoded = data.get("result_base64_encoded")
        if encoded is None:
            raise ApiError(
                f"Champ 'result_base64_encoded' absent de la réponse pour '{cmd}'"
            )
        return base64.b64decode(encoded).decode(errors="replace")

    def batch_cli(self, cmds: list[str]) -> None:
        """
        Soumet une liste de commandes CLI en une seule requête via
        POST /rest/{v}/cli_batch.

        IMPORTANT (voir DESIGN.md) : contrairement à any_cli(), le switch ne
        renvoie que l'ACCEPTATION du batch (statut CBS_INITIATED), pas son
        résultat. Ni la classe PHP d'origine ni le script officiel HPE
        (github.com/aruba/arubaos-switch-api-python) n'implémentent la
        récupération du résultat — ce point reste à vérifier sur un switch
        réel (endpoint de polling à identifier, probablement via le
        catalogue d'URIs exposé par le switch lui-même sous /rest/{v}).
        Pour l'instant, utiliser any_cli() en boucle si le résultat de
        chaque commande est nécessaire.

        Lève CliCommandError si le batch est refusé à la soumission.
        """
        cmd_block = "\n".join(cmds) + "\n"
        encoded = base64.b64encode(cmd_block.encode()).decode()

        response = self._request(
            "POST",
            "cli_batch",
            api_version=self.cli_api_version,
            json_body={"cli_batch_base64_encoded": encoded},
        )
        if response.status_code != 202:
            raise ApiError(
                f"HTTP {response.status_code} inattendu pour batch_cli "
                f"(attendu : 202)",
                http_code=response.status_code,
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise ApiError("Réponse batch_cli illisible (JSON invalide)") from exc

        status = data.get("status")
        if status != "CBS_INITIATED":
            raise CliCommandError(
                f"Erreur API ({status}) pour batch_cli : {data.get('error_msg')}",
                api_status=status,
            )
