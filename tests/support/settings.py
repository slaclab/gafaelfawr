"""Build test settings for Gafaelfawr."""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional, Union

from cryptography.fernet import Fernet

from gafaelfawr.config import Config, OIDCClient
from gafaelfawr.dependencies.config import config_dependency
from gafaelfawr.keypair import RSAKeyPair

from .constants import TEST_DATABASE_URL

__all__ = [
    "build_settings",
    "configure",
    "store_secret",
]


def store_secret(tmp_path: Path, name: str, secret: bytes) -> Path:
    """Store a secret in a temporary path.

    Parameters
    ----------
    tmp_path : `pathlib.Path`
        The root of the temporary area.
    name : `str`
        The name of the secret to construct nice file names.
    secret : `bytes`
        The value of the secret.
    """
    secret_path = tmp_path / name
    secret_path.write_bytes(secret)
    return secret_path


def _build_settings_file(
    tmp_path: Path, template: str, **kwargs: Union[str, Path]
) -> Path:
    """Construct a settings file from a format template.

    Parameters
    ----------
    tmp_path : `pathlib.Path`
        The root of the temporary area.
    template : `str`
        Name of the configuration template to use.
    **kwargs : `str`
        The values to substitute into the template.

    Returns
    -------
    settings_path : `pathlib.Path`
        The path to the newly-constructed configuration file.
    """
    template_file = template + ".yaml.in"
    template_path = Path(__file__).parent.parent / "settings" / template_file
    template = template_path.read_text()
    settings = template.format(**kwargs)
    settings_path = tmp_path / "gafaelfawr.yaml"
    settings_path.write_text(settings)
    return settings_path


def build_settings(
    tmp_path: Path,
    template: str,
    oidc_clients: Optional[List[OIDCClient]] = None,
    **settings: str,
) -> Path:
    """Generate a test Gafaelfawr settings file with secrets.

    Parameters
    ----------
    tmp_path : `pathlib.Path`
        The root of the temporary area.
    template : `str`
        Settings template to use.
    oidc_clients : List[`gafaelfawr.config.OIDCClient`] or `None`
        Configuration information for clients of the OpenID Connect server.
    **settings : `str`
        Any additional settings to add to the settings file.

    Returns
    -------
    settings_path : `pathlib.Path`
        The path of the settings file.
    """
    session_secret = Fernet.generate_key()
    session_secret_file = store_secret(tmp_path, "session", session_secret)
    issuer_key = RSAKeyPair.generate().private_key_as_pem()
    issuer_key_file = store_secret(tmp_path, "issuer", issuer_key)
    influxdb_secret_file = store_secret(tmp_path, "influxdb", b"influx-secret")
    github_secret_file = store_secret(tmp_path, "github", b"github-secret")
    oidc_secret_file = store_secret(tmp_path, "oidc", b"oidc-secret")

    if settings and "database_url" in settings:
        database_url = settings["database_url"]
    else:
        database_url = TEST_DATABASE_URL

    settings_path = _build_settings_file(
        tmp_path,
        template,
        database_url=database_url,
        session_secret_file=session_secret_file,
        issuer_key_file=issuer_key_file,
        github_secret_file=github_secret_file,
        oidc_secret_file=oidc_secret_file,
        influxdb_secret_file=influxdb_secret_file,
    )

    if oidc_clients:
        oidc_path = tmp_path / "oidc.json"
        clients_data = [
            {"id": c.client_id, "secret": c.client_secret}
            for c in oidc_clients
        ]
        oidc_path.write_text(json.dumps(clients_data))
        settings["oidc_server_secrets_file"] = str(oidc_path)

    if settings:
        with settings_path.open("a") as f:
            for key, value in settings.items():
                f.write(f"{key}: {value}\n")

    return settings_path


async def configure(
    tmp_path: Path,
    template: str,
    *,
    oidc_clients: Optional[List[OIDCClient]] = None,
    **settings: str,
) -> Config:
    """Change the test application configuration.

    This cannot be used to change the database URL because sessions will not
    be recreated or the database reinitialized.

    Parameters
    ----------
    tmp_path : `pathlib.Path`
        Root of the test temporary directory, used to write the settings
        file.
    template : `str`
        Settings template to use.
    oidc_clients : List[`gafaelfawr.config.OIDCClient`], optional
        Configuration information for clients of the OpenID Connect server.
    **settings : str, optional
        Any additional settings to add to the settings file.

    Returns
    -------
    config : `gafaelfawr.config.Config`
        The new configuration.
    """
    settings_path = build_settings(
        tmp_path,
        template,
        oidc_clients,
        **settings,
    )
    config_dependency.set_settings_path(str(settings_path))
    return await config_dependency()
