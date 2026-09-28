/**
 * Browser WebAuthn helpers: convert the platform's JSON options (base64url
 * fields) into ArrayBuffers for `navigator.credentials`, and the credential
 * back into JSON the platform verifies. Native is M15-06 (no WebAuthn).
 */

import { Platform } from 'react-native';

function toBuffer(value: string): ArrayBuffer {
  const pad = '='.repeat((4 - (value.length % 4)) % 4);
  const binary = atob(value.replace(/-/g, '+').replace(/_/g, '/') + pad);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  return bytes.buffer;
}

function toBase64url(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  let binary = '';
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

export function passkeysSupported(): boolean {
  if (Platform.OS === 'web') {
    return typeof navigator !== 'undefined' && typeof navigator.credentials?.create === 'function';
  }
  return false;
}

export function creationOptionsFromJson(options: Record<string, unknown>): CredentialCreationOptions {
  const copy = JSON.parse(JSON.stringify(options)) as {
    challenge: string;
    user: { id: string; name: string; displayName: string };
    excludeCredentials?: { id: string; type: string; transports?: string[] }[];
  };
  return {
    publicKey: {
      ...(copy as object),
      challenge: toBuffer(copy.challenge),
      user: { ...copy.user, id: toBuffer(copy.user.id) },
      excludeCredentials: (copy.excludeCredentials ?? []).map((cred) => ({
        ...cred,
        id: toBuffer(cred.id),
      })),
    } as PublicKeyCredentialCreationOptions,
  };
}

export function requestOptionsFromJson(options: Record<string, unknown>): CredentialRequestOptions {
  const copy = JSON.parse(JSON.stringify(options)) as {
    challenge: string;
    allowCredentials?: { id: string; type: string; transports?: string[] }[];
  };
  return {
    publicKey: {
      ...(copy as object),
      challenge: toBuffer(copy.challenge),
      allowCredentials: (copy.allowCredentials ?? []).map((cred) => ({
        ...cred,
        id: toBuffer(cred.id),
      })),
    } as PublicKeyCredentialRequestOptions,
  };
}

export function credentialToJson(credential: PublicKeyCredential): Record<string, unknown> {
  const response = credential.response as AuthenticatorAttestationResponse & AuthenticatorAssertionResponse;
  const attestation = 'attestationObject' in credential.response ? (credential.response as AuthenticatorAttestationResponse) : null;
  const assertion = 'authenticatorData' in credential.response ? (credential.response as AuthenticatorAssertionResponse) : null;
  const transports =
    attestation && typeof attestation.getTransports === 'function' ? attestation.getTransports() : undefined;
  const rawId = toBase64url(credential.rawId);
  return {
    id: rawId,
    rawId,
    type: credential.type,
    response: {
      clientDataJSON: toBase64url(response.clientDataJSON),
      attestationObject: attestation ? toBase64url(attestation.attestationObject) : undefined,
      authenticatorData: assertion ? toBase64url(assertion.authenticatorData) : undefined,
      signature: assertion ? toBase64url(assertion.signature) : undefined,
      userHandle: assertion?.userHandle ? toBase64url(assertion.userHandle) : undefined,
      transports,
    },
    authenticatorAttachment: credential.authenticatorAttachment,
    clientExtensionResults: credential.getClientExtensionResults?.() ?? {},
  };
}

export async function createCredential(options: Record<string, unknown>): Promise<Record<string, unknown>> {
  if (Platform.OS === 'web') {
    if (!passkeysSupported()) throw new Error('passkeys_unavailable');
    const credential = (await navigator.credentials.create(creationOptionsFromJson(options))) as PublicKeyCredential | null;
    if (credential == null) throw new Error('passkey_cancelled');
    return credentialToJson(credential);
  }
  throw new Error('passkeys_unavailable');
}

export async function getCredential(options: Record<string, unknown>): Promise<Record<string, unknown>> {
  if (Platform.OS === 'web') {
    if (!passkeysSupported()) throw new Error('passkeys_unavailable');
    const credential = (await navigator.credentials.get(requestOptionsFromJson(options))) as PublicKeyCredential | null;
    if (credential == null) throw new Error('passkey_cancelled');
    return credentialToJson(credential);
  }
  throw new Error('passkeys_unavailable');
}
