import base64
import stat
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.core.tokens import (
    AUDIENCE,
    ISSUER,
    KEY_FILENAME,
    SigningKey,
    TokenError,
    TokenService,
    jwk_thumbprint,
    load_or_create_signing_key,
)

TTL = timedelta(minutes=5)
CLAIMS = {"sub": "user-1", "sid": "session-1", "role": "member", "act": "user"}

# RFC 8037 appendix A.1 / A.3: Ed25519 test key and its RFC 7638 thumbprint.
RFC8037_D = "nWGxne_9WmC6hEr0kuwsxERJxWl7MmkZcDusAxyuf2A"
RFC8037_X = "11qYAYKxCrfVS_7TyWQHOg7hcvPapiMlrwIaaPcHURo"
RFC8037_THUMBPRINT = "kPrK_qmxVWaYVA9wwBF6Iuo3vVzz7TxHCTwXBygrS4k"


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


@pytest.fixture
def tokens(tmp_path) -> TokenService:
    return TokenService(load_or_create_signing_key(tmp_path / "keys"))


def _forge(tokens: TokenService, payload: dict, **headers) -> str:
    """Sign an arbitrary payload with the service's own private key."""
    key = tokens._signing_key
    return jwt.encode(
        payload, key.private_key, algorithm="EdDSA", headers={"kid": key.kid, **headers}
    )


def _assert_rejected(tokens, token, cause: type[Exception]) -> None:
    with pytest.raises(TokenError) as excinfo:
        tokens.verify_token(token, act="user")
    assert isinstance(excinfo.value.__cause__, cause)


def _valid_payload(**overrides) -> dict:
    now = int(datetime.now(UTC).timestamp())
    return {**CLAIMS, "iss": ISSUER, "aud": AUDIENCE, "iat": now, "exp": now + 300, **overrides}


# --- key management ---------------------------------------------------------


def test_thumbprint_matches_rfc8037_vector():
    assert jwk_thumbprint({"kty": "OKP", "crv": "Ed25519", "x": RFC8037_X}) == RFC8037_THUMBPRINT


def test_kid_is_rfc7638_thumbprint_of_public_key():
    private_key = Ed25519PrivateKey.from_private_bytes(_b64decode(RFC8037_D))
    key = SigningKey.from_private_key(private_key)
    assert key.public_jwk["x"] == RFC8037_X
    assert key.kid == RFC8037_THUMBPRINT


def test_key_generated_with_private_permissions(tmp_path):
    keys_dir = tmp_path / "keys"
    load_or_create_signing_key(keys_dir)
    assert stat.S_IMODE((keys_dir / KEY_FILENAME).stat().st_mode) == 0o600
    assert stat.S_IMODE(keys_dir.stat().st_mode) == 0o700
    assert [p.name for p in keys_dir.iterdir()] == [KEY_FILENAME]


def test_key_survives_reload(tmp_path):
    first = load_or_create_signing_key(tmp_path / "keys")
    second = load_or_create_signing_key(tmp_path / "keys")
    assert first.kid == second.kid
    token = TokenService(first).issue_token(CLAIMS, TTL)
    assert TokenService(second).verify_token(token, act="user")["sub"] == "user-1"


def test_loosened_key_permissions_are_tightened(tmp_path):
    keys_dir = tmp_path / "keys"
    load_or_create_signing_key(keys_dir)
    (keys_dir / KEY_FILENAME).chmod(0o644)
    load_or_create_signing_key(keys_dir)
    assert stat.S_IMODE((keys_dir / KEY_FILENAME).stat().st_mode) == 0o600


def test_separate_dirs_get_separate_keys(tmp_path):
    assert (
        load_or_create_signing_key(tmp_path / "a").kid
        != load_or_create_signing_key(tmp_path / "b").kid
    )


def test_jwks_shape(tokens):
    (jwk,) = tokens.jwks()["keys"]
    assert jwk["kty"] == "OKP"
    assert jwk["crv"] == "Ed25519"
    assert jwk["alg"] == "EdDSA"
    assert jwk["use"] == "sig"
    assert jwk["kid"] == tokens.kid == jwk_thumbprint(jwk)
    assert "d" not in jwk


def test_jwks_alone_is_enough_to_verify(tokens):
    """What a downstream service does: verify using only the published JWKS."""
    token = tokens.issue_token(CLAIMS, TTL)
    jwk_set = jwt.PyJWKSet.from_dict(tokens.jwks())
    signing_key = jwk_set[jwt.get_unverified_header(token)["kid"]]
    claims = jwt.decode(token, signing_key, algorithms=["EdDSA"], audience=AUDIENCE, issuer=ISSUER)
    assert claims["sub"] == "user-1"


# --- issue / verify ---------------------------------------------------------


def test_round_trip(tokens):
    token = tokens.issue_token(CLAIMS, TTL)
    header = jwt.get_unverified_header(token)
    assert header == {"alg": "EdDSA", "kid": tokens.kid, "typ": "JWT"}

    claims = tokens.verify_token(token, act="user")
    assert claims["iss"] == ISSUER
    assert claims["aud"] == AUDIENCE
    assert claims["exp"] - claims["iat"] == TTL.total_seconds()
    assert {k: claims[k] for k in CLAIMS} == CLAIMS


def test_act_may_be_one_of_several(tokens):
    token = tokens.issue_token({**CLAIMS, "act": "agent"}, TTL)
    assert tokens.verify_token(token, act=("user", "agent"))["act"] == "agent"


def test_wrong_act_rejected(tokens):
    token = tokens.issue_token({**CLAIMS, "act": "agent"}, TTL)
    with pytest.raises(TokenError, match="act"):
        tokens.verify_token(token, act="user")


def test_non_string_act_rejected(tokens):
    with pytest.raises(TokenError, match="act"):
        tokens.verify_token(_forge(tokens, _valid_payload(act=["user"])), act="user")


def test_tampered_signature_rejected(tokens):
    header, payload, signature = tokens.issue_token(CLAIMS, TTL).split(".")
    mid = len(signature) // 2
    flipped = "A" if signature[mid] != "A" else "B"
    tampered = f"{header}.{payload}.{signature[:mid]}{flipped}{signature[mid + 1 :]}"
    _assert_rejected(tokens, tampered, jwt.InvalidSignatureError)


def test_tampered_payload_rejected(tokens):
    header, _, signature = tokens.issue_token(CLAIMS, TTL).split(".")
    _, forged_payload, _ = tokens.issue_token({**CLAIMS, "role": "admin"}, TTL).split(".")
    _assert_rejected(tokens, f"{header}.{forged_payload}.{signature}", jwt.InvalidSignatureError)


def test_expired_rejected(tokens):
    token = tokens.issue_token(CLAIMS, TTL, now=datetime.now(UTC) - timedelta(minutes=10))
    _assert_rejected(tokens, token, jwt.ExpiredSignatureError)


def test_wrong_audience_rejected(tokens):
    _assert_rejected(tokens, _forge(tokens, _valid_payload(aud="other")), jwt.InvalidAudienceError)


def test_wrong_issuer_rejected(tokens):
    token = _forge(tokens, _valid_payload(iss="someone-else"))
    _assert_rejected(tokens, token, jwt.InvalidIssuerError)


@pytest.mark.parametrize("claim", ["exp", "iat", "sub", "act", "aud", "iss"])
def test_missing_required_claim_rejected(tokens, claim):
    payload = _valid_payload()
    del payload[claim]
    _assert_rejected(tokens, _forge(tokens, payload), jwt.MissingRequiredClaimError)


def test_unknown_kid_rejected(tokens, tmp_path):
    other = TokenService(load_or_create_signing_key(tmp_path / "other-keys"))
    with pytest.raises(TokenError, match="unknown kid"):
        tokens.verify_token(other.issue_token(CLAIMS, TTL), act="user")


def test_missing_kid_rejected(tokens):
    token = jwt.encode(_valid_payload(), tokens._signing_key.private_key, algorithm="EdDSA")
    with pytest.raises(TokenError, match="unknown kid"):
        tokens.verify_token(token, act="user")


def test_known_kid_signed_by_other_key_rejected(tokens):
    token = jwt.encode(
        _valid_payload(),
        Ed25519PrivateKey.generate(),
        algorithm="EdDSA",
        headers={"kid": tokens.kid},
    )
    _assert_rejected(tokens, token, jwt.InvalidSignatureError)


def test_hmac_alg_confusion_rejected(tokens):
    """HS256 "signed" with the public key bytes must not verify."""
    token = jwt.encode(
        _valid_payload(),
        tokens._signing_key.public_jwk["x"],
        algorithm="HS256",
        headers={"kid": tokens.kid},
    )
    with pytest.raises(TokenError):
        tokens.verify_token(token, act="user")


def test_alg_none_rejected(tokens):
    token = jwt.encode(_valid_payload(), None, algorithm="none", headers={"kid": tokens.kid})
    with pytest.raises(TokenError):
        tokens.verify_token(token, act="user")


def test_garbage_rejected(tokens):
    with pytest.raises(TokenError, match="malformed"):
        tokens.verify_token("not-a-jwt", act="user")


@pytest.mark.parametrize("claim", ["iss", "aud", "iat", "exp", "nbf"])
def test_issue_refuses_reserved_claims(tokens, claim):
    with pytest.raises(ValueError, match="sets"):
        tokens.issue_token({**CLAIMS, claim: "x"}, TTL)


@pytest.mark.parametrize("claim", ["sub", "act"])
def test_issue_requires_sub_and_act(tokens, claim):
    claims = dict(CLAIMS)
    del claims[claim]
    with pytest.raises(ValueError, match="requires"):
        tokens.issue_token(claims, TTL)
