import base64
from urllib.parse import parse_qs, urlparse

from app.core import totp

# RFC 6238 appendix B: SHA-1 seed "12345678901234567890"; 8-digit codes, of
# which the 6-digit code is the last six digits.
RFC_SECRET = base64.b32encode(b"12345678901234567890").decode().rstrip("=")
RFC_VECTORS = {
    59: "94287082",
    1111111109: "07081804",
    1234567890: "89005924",
    2000000000: "69279037",
}


def test_rfc6238_vectors():
    for t, expected in RFC_VECTORS.items():
        assert totp.code_for(RFC_SECRET, now=t) == expected[-6:]


def test_match_accepts_adjacent_steps_only():
    now = 1234567890
    step = totp.current_step(now)
    for offset in (-1, 0, 1):
        code = totp.code_for(RFC_SECRET, now=now + offset * totp.PERIOD_S)
        assert totp.match_step(RFC_SECRET, code, now=now) == step + offset
    far = totp.code_for(RFC_SECRET, now=now + 3 * totp.PERIOD_S)
    assert totp.match_step(RFC_SECRET, far, now=now) is None


def test_match_refuses_used_steps():
    now = 1234567890
    code = totp.code_for(RFC_SECRET, now=now)
    step = totp.match_step(RFC_SECRET, code, now=now)
    assert totp.match_step(RFC_SECRET, code, after_step=step, now=now) is None
    assert totp.match_step(RFC_SECRET, code, after_step=step - 1, now=now) == step


def test_match_rejects_malformed():
    for code in ("", "12345", "1234567", "abcdef", "12 34 5x"):
        assert totp.match_step(RFC_SECRET, code, now=59) is None
    spaced = totp.code_for(RFC_SECRET, now=59)
    assert totp.match_step(RFC_SECRET, f" {spaced[:3]} {spaced[3:]} ", now=59) is not None


def test_generated_secret_round_trips():
    secret = totp.generate_secret()
    assert len(base64.b32decode(secret + "=" * (-len(secret) % 8))) == 20
    assert totp.match_step(secret, totp.code_for(secret)) is not None


def test_provisioning_uri():
    uri = urlparse(totp.provisioning_uri("ABCDEF", "alice"))
    assert (uri.scheme, uri.netloc, uri.path) == ("otpauth", "totp", "/HomeAI:alice")
    query = {k: v[0] for k, v in parse_qs(uri.query).items()}
    assert query == {
        "secret": "ABCDEF",
        "issuer": "HomeAI",
        "algorithm": "SHA1",
        "digits": "6",
        "period": "30",
    }
