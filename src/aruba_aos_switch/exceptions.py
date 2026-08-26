"""Exceptions levées par aruba_aos_switch."""

from __future__ import annotations


class AosSwitchError(Exception):
    """Classe de base pour toutes les erreurs de la librairie."""


class ConnectionError(AosSwitchError):
    """Le switch n'a pas pu être joint (réseau, timeout, TLS...)."""


class AuthenticationError(AosSwitchError):
    """Échec de login (identifiants refusés, session invalide...)."""


class ApiError(AosSwitchError):
    """
    La requête a atteint le switch mais l'API a répondu par une erreur
    (code HTTP inattendu, ou statut d'erreur dans le corps JSON).
    """

    def __init__(
        self,
        message: str,
        *,
        http_code: int | None = None,
        api_status: str | None = None,
    ) -> None:
        super().__init__(message)
        self.http_code = http_code
        self.api_status = api_status


class CliCommandError(ApiError):
    """
    Une commande envoyée via any_cli()/batch_cli() a été refusée par le
    switch (ex: syntaxe invalide, statut != CCS_SUCCESS).
    """
