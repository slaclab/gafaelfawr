"""Utility functions for tests."""

from __future__ import annotations

import base64
import os
from asyncio import Future
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING
from unittest.mock import Mock

import jwt
import mockaioredis
from aiohttp import ClientResponse, web
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

from jwt_authorizer.app import create_app
from jwt_authorizer.config import ALGORITHM
from jwt_authorizer.verify import KeyClient

if TYPE_CHECKING:
    from typing import Any, Dict, List, Optional


def number_to_base64(data: int) -> bytes:
    """Convert an integer to base64-encoded bytes in big endian order.

    Parameters
    ----------
    data : `int`
        Arbitrarily large number

    Returns
    -------
    result : `bytes`
        The equivalent URL-safe base64-encoded string corresponding to the
        number in big endian order.
    """
    bit_length = data.bit_length()
    byte_length = bit_length // 8 + 1
    data_as_bytes = data.to_bytes(byte_length, byteorder="big", signed=False)
    return base64.urlsafe_b64encode(data_as_bytes)


class FakeKeyClient(KeyClient):
    """Override KeyClient to not make HTTP requests.

    This returns minimal OpenID Connect and JWKS metadata for the two issuers
    used by the test suite.
    """

    def __init__(self, keypair: RSAKeyPair) -> None:
        self.keypair = keypair

    async def get_url(self, url: str) -> ClientResponse:
        if url == "https://test.example.com/.well-known/openid-configuration":
            jwks_uri = "https://test.example.com/.well-known/jwks.json"
            return self._build_response_success({"jwks_uri": jwks_uri})
        elif url == "https://test.example.com/.well-known/jwks.json":
            return self._build_response_success(self._build_keys("some-kid"))
        elif url == "https://orig.example.com/.well-known/jwks.json":
            return self._build_response_success(self._build_keys("orig-kid"))
        else:
            return self._build_response_failure()

    def _build_keys(self, kid: str) -> Dict[str, Any]:
        """Generate the JSON-encoded keys structure for a keypair."""
        public_numbers = self.keypair.public_numbers()
        e = number_to_base64(public_numbers.e).decode()
        n = number_to_base64(public_numbers.n).decode()
        return {"keys": [{"alg": ALGORITHM, "e": e, "n": n, "kid": kid}]}

    def _build_response_failure(self) -> ClientResponse:
        """Build a successful response."""
        r = Mock(spec=ClientResponse)
        r.status = 404
        return r

    def _build_response_success(
        self, result: Dict[str, Any]
    ) -> ClientResponse:
        """Build a successful response."""
        r = Mock(spec=ClientResponse)
        future: Future[Dict[str, Any]] = Future()
        future.set_result(result)
        r.status = 200
        r.json.return_value = future
        return r


class RSAKeyPair:
    """An autogenerated public/private key pair."""

    def __init__(self) -> None:
        self.private_key = rsa.generate_private_key(
            public_exponent=65537, key_size=2048, backend=default_backend()
        )

    def private_key_as_pem(self) -> bytes:
        return self.private_key.private_bytes(
            Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()
        )

    def public_key_as_pem(self) -> bytes:
        return self.private_key.public_key().public_bytes(
            Encoding.PEM, PublicFormat.SubjectPublicKeyInfo,
        )

    def public_numbers(self) -> rsa.RSAPublicNumbers:
        return self.private_key.public_key().public_numbers()


async def create_test_app(
    keypair: Optional[RSAKeyPair] = None,
    session_secret: Optional[bytes] = None,
    **kwargs: Any,
) -> web.Application:
    """Configured aiohttp Application for testing."""
    if not keypair:
        keypair = RSAKeyPair()
    if not session_secret:
        session_secret = os.urandom(16)

    kwargs["OAUTH2_JWT.KEY"] = keypair.private_key_as_pem().decode()
    secret_b64 = base64.urlsafe_b64encode(session_secret).decode()
    kwargs["OAUTH2_STORE_SESSION.OAUTH2_PROXY_SECRET"] = secret_b64
    kwargs["OAUTH2_STORE_SESSION.REDIS_URL"] = "dummy"

    app = await create_app(
        redis_pool=await mockaioredis.create_redis_pool(""),
        key_client=FakeKeyClient(keypair),
        FORCE_ENV_FOR_DYNACONF="testing",
        **kwargs,
    )

    return app


def create_test_token(
    keypair: RSAKeyPair,
    groups: Optional[List[str]] = None,
    kid: str = "some-kid",
    **attributes: str,
) -> str:
    """Create a signed token using the configured test issuer.

    This will match the issuer and audience of the default JWT Authorizer
    issuer, so JWT Authorizer will not attempt to reissue it.

    Parameters
    ----------
    keypair : `RSAKeyPair`
        The key pair to use to sign the token.
    groups : List[`str`], optional
        Group memberships the generated token should have.
    kid : `str`
        The key ID to use.
    **attributes : `str`
        Other attributes to set or override in the token.

    Returns
    -------
    token : `str`
        The encoded token.
    """
    payload = create_test_token_payload(groups, **attributes)
    return jwt.encode(
        payload,
        keypair.private_key_as_pem(),
        algorithm=ALGORITHM,
        headers={"kid": kid},
    ).decode()


def create_test_token_payload(
    groups: Optional[List[str]] = None, **attributes: str,
) -> Dict[str, Any]:
    """Create the contents of a token using the configured test issuer.

    This will match the issuer and audience of the default JWT Authorizer
    issuer, so JWT Authorizer will not attempt to reissue it.

    Parameters
    ----------
    groups : List[`str`], optional
        Group memberships the generated token should have.
    **attributes : `str`
        Other attributes to set or override in the token.

    Returns
    -------
    payload : Dict[`str`, Any]
        The contents of the token.
    """
    exp = datetime.now(timezone.utc) + timedelta(days=24)
    payload: Dict[str, Any] = {
        "aud": "https://example.com/",
        "email": "some-user@example.com",
        "exp": int(exp.timestamp()),
        "iss": "https://test.example.com/",
        "jti": "some-unique-id",
        "sub": "some-user",
        "uid": "some-user",
        "uidNumber": "1000",
    }
    payload.update(attributes)
    if groups:
        payload["isMemberOf"] = [{"name": g} for g in groups]
    return payload
