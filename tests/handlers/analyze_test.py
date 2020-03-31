"""Tests for the /auth/analyze route."""

from __future__ import annotations

import base64
import os
import time
from datetime import datetime
from typing import TYPE_CHECKING
from unittest.mock import ANY

import jwt

from jwt_authorizer.config import ALGORITHM
from jwt_authorizer.session import Ticket
from tests.util import RSAKeyPair, create_test_app, create_test_token

if TYPE_CHECKING:
    from aiohttp.pytest_plugin.test_utils import TestClient


async def test_analyze_ticket(aiohttp_client: TestClient) -> None:
    ticket = Ticket()
    keypair = RSAKeyPair()
    ticket = Ticket()
    ticket_handle = ticket.encode("oauth2_proxy")
    ticket_b64 = base64.urlsafe_b64encode(ticket_handle.encode()).decode()
    cookie = f"{ticket_b64}|32132781|blahblah"
    token = create_test_token(
        keypair,
        ["admin"],
        kid="orig-kid",
        aud="https://test.example.com/",
        iss="https://orig.example.com/",
    )
    session_secret = os.urandom(16)
    app = await create_test_app(keypair, session_secret)
    client = await aiohttp_client(app)

    # To test, we need a valid ticket.  The existing code path that creates
    # one is the code path that reissues a JWT based on one from an external
    # authentication source.  Run that code path.
    r = await client.get(
        "/auth",
        params={"capability": "exec:admin"},
        headers={"Authorization": f"Bearer {token}"},
        cookies={"oauth2_proxy": cookie},
    )
    assert r.status == 200
    token = r.headers["X-Auth-Request-Token"]

    # Now pass that token to the /auth/analyze endpoint.
    r = await client.post(
        "/auth/analyze", data={"token": ticket.encode("oauth2_proxy")},
    )

    # Check that the results from /analyze include the ticket, the session,
    # and the token information.
    assert r.status == 200
    analysis = await r.json()
    assert analysis == {
        "ticket": {
            "ticket_id": ticket.ticket_id,
            "secret": base64.urlsafe_b64encode(ticket.secret).decode(),
        },
        "session": {
            "email": "some-user@example.com",
            "user": "some-user@example.com",
            "created_at": ANY,
            "expires_on": ANY,
        },
        "token": {
            "header": {"alg": ALGORITHM, "typ": "JWT", "kid": "some-kid"},
            "data": {
                "act": {
                    "aud": "https://test.example.com/",
                    "iss": "https://orig.example.com/",
                    "jti": "some-unique-id",
                },
                "aud": "https://example.com/",
                "email": "some-user@example.com",
                "exp": ANY,
                "iat": ANY,
                "isMemberOf": [{"name": "admin"}],
                "iss": "https://test.example.com/",
                "jti": ticket.as_handle("oauth2_proxy"),
                "scope": "exec:admin read:all",
                "sub": "some-user",
                "uid": "some-user",
                "uidNumber": "1000",
            },
            "valid": True,
        },
    }
    created_at = datetime.strptime(
        analysis["session"]["created_at"], "%Y-%m-%d %H:%M:%S %z"
    )
    expires_on = datetime.strptime(
        analysis["session"]["expires_on"], "%Y-%m-%d %H:%M:%S %z"
    )

    now = time.time()
    assert now - 5 <= created_at.timestamp() <= now + 5
    assert int(expires_on.timestamp()) == analysis["token"]["data"]["exp"]


async def test_analyze_token(aiohttp_client: TestClient) -> None:
    payload = {
        "aud": "https://test.example.com/",
        "email": "some-user@example.com",
        "iss": "https://orig.example.com/",
        "jti": "some-unique-id",
        "kid": "orig-kid",
        "sub": "some-user",
        "uidNumber": "1000",
    }
    keypair = RSAKeyPair()
    app = await create_test_app(keypair, os.urandom(16))
    client = await aiohttp_client(app)

    # Generate a token that we can analyze.
    token = jwt.encode(
        payload,
        keypair.private_key_as_pem(),
        algorithm=ALGORITHM,
        headers={"kid": "orig-kid"},
    )

    # Analyze it.
    r = await client.post("/auth/analyze", data={"token": token.decode()})
    assert r.status == 200
    assert await r.json() == {
        "token": {
            "header": jwt.get_unverified_header(token),
            "data": payload,
            "valid": True,
        },
    }
