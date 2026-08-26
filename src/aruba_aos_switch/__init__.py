"""
aruba_aos_switch : client Python pour l'API REST des switchs HPE Aruba
Networking sous ArubaOS-Switch (AOS-S).
"""

from .client import AosSwitchClient
from .exceptions import (
    ApiError,
    AosSwitchError,
    AuthenticationError,
    CliCommandError,
    ConnectionError,
)

__all__ = [
    "AosSwitchClient",
    "AosSwitchError",
    "AuthenticationError",
    "ApiError",
    "CliCommandError",
    "ConnectionError",
]

__version__ = "0.1.0"
