"""`JwksIdentityVerifier` (app/core/identity.py) against a local key pair."""

from __future__ import annotations

from datetime import timedelta

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.core.identity import IdentityError, KeysUnavailable
from tests.fake_identity import LocalKeyPair

USER = "3f0b8e7e-8d1c-4d6e-9d55-8d9a6c1f0a01"


@pytest.fixture
def keys() -> LocalKeyPair:
    return LocalKeyPair()


async def test_valid_token(keys: LocalKeyPair) -> None:
    identity = await keys.verifier().verify(keys.token(USER))
    assert identity.user_id == USER
    assert identity.session_id == "11111111-1111-4111-8111-111111111111"
    assert identity.role == "member"


async def test_jwks_fetched_once_and_cached(keys: LocalKeyPair) -> None:
    verifier = keys.verifier()
    for _ in range(3):
        await verifier.verify(keys.token(USER))
    assert keys.fetches == 1


async def test_nothing_fetched_until_first_use(keys: LocalKeyPair) -> None:
    keys.verifier()
    assert keys.fetches == 0


@pytest.mark.parametrize(
    "make_token",
    [
        pytest.param(lambda k: k.token(USER, signing_key=Ed25519PrivateKey.generate()), id="forged"),
        pytest.param(lambda k: k.token(USER, ttl=timedelta(seconds=-1)), id="expired"),
        pytest.param(lambda k: k.token(USER, aud="someone-else"), id="wrong-aud"),
        pytest.param(lambda k: k.token(USER, iss="not-the-platform"), id="wrong-iss"),
        pytest.param(lambda k: k.token(USER, act="agent"), id="act-agent"),
        pytest.param(lambda k: k.token(USER, drop=("act",)), id="no-act"),
        pytest.param(lambda k: k.token(USER, drop=("exp",)), id="no-exp"),
        pytest.param(lambda k: k.token("not-a-uuid"), id="sub-not-uuid"),
        pytest.param(lambda k: k.token(USER)[:-4] + "AAAA", id="bad-signature"),
        pytest.param(lambda k: "not.a.jwt", id="malformed"),
        pytest.param(lambda k: "", id="empty"),
        pytest.param(lambda k: None, id="missing"),
    ],
)
async def test_rejected_tokens(keys: LocalKeyPair, make_token) -> None:
    with pytest.raises(IdentityError):
        await keys.verifier().verify(make_token(keys))


async def test_alg_none_rejected(keys: LocalKeyPair) -> None:
    token = jwt.encode(
        {"iss": "homeai-platform", "aud": "homeai", "sub": USER, "act": "user", "iat": 0,
         "exp": 4102444800},
        key=None,
        algorithm="none",
        headers={"kid": keys.kid},
    )  # fmt: skip
    with pytest.raises(IdentityError):
        await keys.verifier().verify(token)


async def test_hs256_with_public_key_rejected(keys: LocalKeyPair) -> None:
    token = jwt.encode(
        {"iss": "homeai-platform", "aud": "homeai", "sub": USER, "act": "user", "iat": 0,
         "exp": 4102444800},
        key=keys.jwk["x"],
        algorithm="HS256",
        headers={"kid": keys.kid},
    )  # fmt: skip
    with pytest.raises(IdentityError):
        await keys.verifier().verify(token)


async def test_unknown_kid_refetches_then_accepts_rotated_key(keys: LocalKeyPair) -> None:
    clock = [0.0]
    verifier = keys.verifier(clock=lambda: clock[0])
    await verifier.verify(keys.token(USER))

    rotated = LocalKeyPair()
    keys.jwk = rotated.jwk
    clock[0] = 60.0
    identity = await verifier.verify(rotated.token(USER))
    assert identity.user_id == USER
    assert keys.fetches == 2


async def test_unknown_kid_refetch_is_rate_limited(keys: LocalKeyPair) -> None:
    clock = [0.0]
    verifier = keys.verifier(clock=lambda: clock[0], refetch_interval_s=10)
    await verifier.verify(keys.token(USER))
    for i in range(5):
        with pytest.raises(IdentityError):
            await verifier.verify(keys.token(USER, kid=f"forged-{i}"))
    assert keys.fetches == 1

    clock[0] = 11.0
    with pytest.raises(IdentityError):
        await verifier.verify(keys.token(USER, kid="forged-late"))
    assert keys.fetches == 2


async def test_fetch_failure_is_keys_unavailable_not_a_bad_token(keys: LocalKeyPair) -> None:
    clock = [0.0]
    verifier = keys.verifier(clock=lambda: clock[0])
    keys.fail_fetch = True
    with pytest.raises(KeysUnavailable):
        await verifier.verify(keys.token(USER))
    with pytest.raises(KeysUnavailable):
        await verifier.verify(keys.token(USER))
    assert keys.fetches == 1

    keys.fail_fetch = False
    clock[0] = 11.0
    assert (await verifier.verify(keys.token(USER))).user_id == USER


async def test_failed_refetch_keeps_known_keys(keys: LocalKeyPair) -> None:
    clock = [0.0]
    verifier = keys.verifier(clock=lambda: clock[0])
    await verifier.verify(keys.token(USER))
    keys.fail_fetch = True
    clock[0] = 60.0
    with pytest.raises(KeysUnavailable):
        await verifier.verify(keys.token(USER, kid="unknown"))
    assert (await verifier.verify(keys.token(USER))).user_id == USER
