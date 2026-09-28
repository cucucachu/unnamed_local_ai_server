import {
  APP_CONTEXT_HEADER,
  displayUserPrompt,
  formatAppContext,
  loadAppContext,
  messageHasAppContext,
  seedUserMessage,
  type AppContext,
} from '../appAgent';
import type { Space } from '../platform';

const INSTANCE = '11111111-1111-4111-8111-111111111111';
const APP = '22222222-2222-4222-8222-222222222222';
const space: Space = {
  id: 's1',
  slug: 'family',
  name: 'Family',
  kind: 'shared',
  gid: 3000,
  owner_user_id: null,
  role: 'owner',
  created_at: '',
  archived_at: null,
};

const ctx: AppContext = {
  instanceId: INSTANCE,
  space: { id: space.id, slug: space.slug, name: space.name, role: space.role },
  appId: APP,
  appName: 'Runtime check',
  sourcePath: '/spaces/family/Apps/runtime-check',
  files: {
    'app.json': '{"name":"Runtime check"}',
    'AGENT.md': '# Runtime check\nA list of items.',
    'schema.sql': 'CREATE TABLE items (id INTEGER PRIMARY KEY);',
  },
};

describe('appAgent context', () => {
  it('formats instance, space, and the three source files', () => {
    const text = formatAppContext(ctx);
    expect(text).toContain(APP_CONTEXT_HEADER);
    expect(text).toContain(INSTANCE);
    expect(text).toContain('family (Family, role owner)');
    expect(text).toContain('# Runtime check');
    expect(text).toContain('CREATE TABLE items');
    expect(text).toContain('{"name":"Runtime check"}');
  });

  it('seeds the first user message and strips it for display', () => {
    const wire = seedUserMessage(ctx, 'Add Milk via app_sql');
    expect(messageHasAppContext(wire)).toBe(true);
    expect(wire).toContain('Add Milk via app_sql');
    expect(displayUserPrompt(wire)).toBe('Add Milk via app_sql');
    expect(displayUserPrompt('just a question')).toBe('just a question');
  });

  it('loads the source files from the app folder', async () => {
    const files: Record<string, string> = {
      '/spaces/family/Apps/runtime-check/app.json': '{"slug":"runtime-check"}',
      '/spaces/family/Apps/runtime-check/AGENT.md': 'what it does',
      '/spaces/family/Apps/runtime-check/schema.sql': 'CREATE TABLE items (id INTEGER);',
    };
    global.fetch = jest.fn(async (url: string, init?: RequestInit) => {
      const path = String(url).replace(/^https?:\/\/[^/]+/, '');
      if (path === `/api/platform/apps/${APP}` && (init?.method ?? 'GET') === 'GET') {
        return { ok: true, status: 200, json: async () => ({ id: APP, slug: 'runtime-check', name: 'Runtime check', source_space_id: 's1', source_path: '/spaces/family/Apps/runtime-check', working_version: null }) };
      }
      const match = path.match(/path=([^&]+)/);
      const filePath = match ? decodeURIComponent(match[1]) : '';
      if (path.startsWith('/api/platform/files/stream') && files[filePath] !== undefined) {
        return { ok: true, status: 200, text: async () => files[filePath], json: async () => ({}) };
      }
      return { ok: false, status: 404, json: async () => ({ detail: 'not_found' }), text: async () => '' };
    }) as unknown as typeof fetch;

    const loaded = await loadAppContext(INSTANCE, space, APP, 'Runtime check');
    expect(loaded.sourcePath).toBe('/spaces/family/Apps/runtime-check');
    expect(loaded.files['AGENT.md']).toBe('what it does');
    expect(loaded.files['app.json']).toContain('runtime-check');
    expect(loaded.files['schema.sql']).toContain('CREATE TABLE items');
  });
});
