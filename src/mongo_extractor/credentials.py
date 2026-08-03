from __future__ import annotations

"""
Compatibilidad hacia atras.

Este modulo conserva los nombres originales mientras delega la logica real
al helper publico y reusable en `mongo_extractor.secret_loader`.
"""

from mongo_extractor.secret_loader import (
    normalize_plain_secret,
    parse_credentials_secret,
    read_system_env_value,
    read_windows_env_value_from_registry,
    resolve_secret_reference,
    resolve_secret_reference_from_keyring_manager,
)

read_windows_env_from_registry = read_windows_env_value_from_registry
get_env_value = read_system_env_value
resolve_credentials_from_keyring_manager = resolve_secret_reference_from_keyring_manager
resolve_credentials_reference = resolve_secret_reference

__all__ = [
    "get_env_value",
    "normalize_plain_secret",
    "parse_credentials_secret",
    "read_windows_env_from_registry",
    "read_system_env_value",
    "read_windows_env_value_from_registry",
    "resolve_credentials_from_keyring_manager",
    "resolve_credentials_reference",
    "resolve_secret_reference",
    "resolve_secret_reference_from_keyring_manager",
]
