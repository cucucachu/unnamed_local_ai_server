# Hardware-backed host-app signing (M15-06)

Local Expo module autolinked from `services/frontend`. Protocol tests
(`services/platform/tests/test_device_pairs.py`) use software P-256 keys
and **do not** load this module.

## Algorithm (must match pytest)

- Curve: NIST P-256 (`secp256r1`)
- Public key: X.509 SPKI DER, unpadded base64url
- Signature: DER ECDSA over SHA-256 of the raw challenge bytes
  (`SHA256withECDSA` / `cryptography` `ECDSA(SHA256)`)
- Android Keystore: `userAuthenticationRequired` so a biometric (or device
  credential) unlock is required before `sign`. JS also calls
  `expo-local-authentication` so the prompt is consistent; the key stays
  usable for a short validity window after unlock.

Private keys never leave the Keystore and are never logged.

## Android

`HomeAIDeviceKeyModule.kt` generates an `AndroidKeyStore` EC key named
`homeai-host-pair`, exports the SPKI public key, and signs challenges.

## iOS

Stub only (M15-06 is Android first). Methods throw `ios_not_implemented`.
Secure Enclave support is a later ticket; no ipa in this one.

## Tests

Jest mocks `@/lib/deviceKey`. Do not call this module from pytest.
