import { apiFetch } from './api';

/**
 * Typed client for `GET`/`PUT /api/settings` (M8-02,
 * `services/agent-server/app/api/settings.py`). Do not deviate from these
 * shapes; mirrors that module's pydantic DTOs (`SettingsDocument`,
 * `SettingsUpdateBody`) field-for-field, and follows `threads.ts`'s
 * established client-module style (docstring citing the exact contract,
 * reusing `apiFetch` for every JSON call).
 */

export type EditModeDefault = 'truncate' | 'fork';

export interface SettingsDocument {
  hitl_enabled: boolean;
  thinking_enabled: boolean;
  edit_mode_default: EditModeDefault;
}

/** Any subset of `SettingsDocument`'s fields — what `PUT /api/settings`
 * accepts. An unknown extra key or a wrong type/invalid literal value gets
 * a `422` from the server (see `SettingsUpdateBody`'s `extra="forbid"` +
 * `strict=True`). */
export type SettingsPartial = Partial<SettingsDocument>;

/** `GET /api/settings` — the full document, server-side defaults applied
 * for any key not yet stored. */
export async function getSettings(): Promise<SettingsDocument> {
  return apiFetch<SettingsDocument>('/api/settings');
}

/** `PUT /api/settings` — accepts a partial document, returns the full
 * server-merged document. */
export async function updateSettings(partial: SettingsPartial): Promise<SettingsDocument> {
  return apiFetch<SettingsDocument>('/api/settings', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(partial),
  });
}
