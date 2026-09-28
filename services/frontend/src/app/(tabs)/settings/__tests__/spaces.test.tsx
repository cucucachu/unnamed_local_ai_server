import { act, type ReactTestRenderer } from 'react-test-renderer';

import type { Member, Space } from '@/lib/platform';

import { exists, isDisabled, mockFetchRoutes, press, render, requestsTo, textOf, type } from '../../../../../test-utils/screen';

const mockPush = jest.fn();
const mockBack = jest.fn();
let mockSpaceId = 'sp-shared';
jest.mock('expo-router', () => ({
  useRouter: () => ({ back: mockBack, push: mockPush, replace: jest.fn(), canGoBack: () => true }),
  useLocalSearchParams: () => ({ spaceId: mockSpaceId }),
  useFocusEffect: jest.fn(),
}));

jest.mock('@/components/AuthProvider', () => ({
  useAuth: () => ({ state: { phase: 'ready', setupRequired: false, user: { id: 'u1', username: 'alice' } } }),
}));

const mockWithStepUp = jest.fn((fn: () => Promise<unknown>) => fn());
jest.mock('@/components/StepUpProvider', () => ({
  useStepUp: () => ({ withStepUp: mockWithStepUp }),
}));

// eslint-disable-next-line import/first -- must follow the jest.mock calls above
import SpacesScreen from '../spaces';
// eslint-disable-next-line import/first -- must follow the jest.mock calls above
import SpaceDetailScreen from '../spaces/[spaceId]';

function space(overrides: Partial<Space>): Space {
  return {
    id: 'sp-shared',
    slug: 'family',
    name: 'Family',
    kind: 'shared',
    gid: 20001,
    owner_user_id: null,
    role: 'owner',
    created_at: '2026-01-01T00:00:00Z',
    archived_at: null,
    ...overrides,
  };
}

const PERSONAL = space({ id: 'sp-personal', slug: 'alice', name: 'Alice', kind: 'personal', owner_user_id: 'u1' });
const ALICE_OWNER: Member = { user_id: 'u1', username: 'alice', display_name: 'Alice', role: 'owner', added_at: '' };
const BOB_VIEWER: Member = { user_id: 'u2', username: 'bob', display_name: 'Bob', role: 'viewer', added_at: '' };

let renderer: ReactTestRenderer | null = null;

beforeEach(() => {
  mockPush.mockReset();
  mockBack.mockReset();
  mockWithStepUp.mockClear();
  mockSpaceId = 'sp-shared';
});

afterEach(() => {
  act(() => renderer?.unmount());
  renderer = null;
});

describe('SpacesScreen', () => {
  it('lists my spaces with their role and opens one', async () => {
    mockFetchRoutes({ 'GET /api/platform/spaces': { body: { spaces: [PERSONAL, space({ role: 'editor' })] } } });
    renderer = await render(SpacesScreen);

    expect(exists(renderer, 'space-row-alice')).toBe(true);
    expect(exists(renderer, 'space-row-family')).toBe(true);
    expect(textOf(renderer)).toContain('editor');
    await press(renderer, 'space-row-family');
    expect(mockPush).toHaveBeenCalledWith({ pathname: '/settings/spaces/[spaceId]', params: { spaceId: 'sp-shared' } });
  });

  it('creates a shared space with a slug derived from the name, then opens it', async () => {
    const fetchMock = mockFetchRoutes({
      'GET /api/platform/spaces': { body: { spaces: [PERSONAL] } },
      'POST /api/platform/spaces': { status: 201, body: space({ id: 'sp-new', slug: 'family-photos' }) },
    });
    renderer = await render(SpacesScreen);

    expect(isDisabled(renderer, 'space-create-submit')).toBe(true);
    await type(renderer, 'space-create-name', 'Family Photos');
    await press(renderer, 'space-create-submit');

    expect(requestsTo(fetchMock, 'POST', '/api/platform/spaces')).toEqual([{ name: 'Family Photos', slug: 'family-photos' }]);
    expect(mockPush).toHaveBeenCalledWith({ pathname: '/settings/spaces/[spaceId]', params: { spaceId: 'sp-new' } });
  });

  it('shows slug_taken', async () => {
    mockFetchRoutes({
      'GET /api/platform/spaces': { body: { spaces: [] } },
      'POST /api/platform/spaces': { status: 409, body: { detail: 'slug_taken' } },
    });
    renderer = await render(SpacesScreen);
    await type(renderer, 'space-create-name', 'Family');
    await type(renderer, 'space-create-slug', 'family');
    await press(renderer, 'space-create-submit');

    expect(textOf(renderer)).toContain('That short name is taken.');
    expect(mockPush).not.toHaveBeenCalled();
  });
});

describe('SpaceDetailScreen', () => {
  it('an owner adds a member from the directory with a role', async () => {
    let members = [ALICE_OWNER];
    const fetchMock = mockFetchRoutes({
      'GET /api/platform/spaces/sp-shared': { body: space({}) },
      'GET /api/platform/spaces/sp-shared/members': () => ({ body: { members } }),
      'GET /api/platform/users/directory': {
        body: { users: [{ id: 'u1', username: 'alice', display_name: 'Alice' }, { id: 'u2', username: 'bob', display_name: 'Bob' }] },
      },
      'POST /api/platform/spaces/sp-shared/members': (body) => {
        members = [ALICE_OWNER, { ...BOB_VIEWER, role: 'editor' }];
        return { status: 201, body };
      },
    });
    renderer = await render(SpaceDetailScreen);

    await press(renderer, 'space-add-member');
    expect(exists(renderer, 'add-member-user-alice')).toBe(false);
    expect(isDisabled(renderer, 'add-member-submit')).toBe(true);
    await press(renderer, 'add-member-user-bob');
    await press(renderer, 'add-member-submit');

    expect(requestsTo(fetchMock, 'POST', '/spaces/sp-shared/members')).toEqual([{ user_id: 'u2', role: 'editor' }]);
    expect(mockWithStepUp).toHaveBeenCalled();
    expect(exists(renderer, 'add-member-modal')).toBe(false);
    expect(exists(renderer, 'member-row-bob')).toBe(true);
  });

  it('an owner changes a role and removes a member', async () => {
    const fetchMock = mockFetchRoutes({
      'GET /api/platform/spaces/sp-shared': { body: space({}) },
      'GET /api/platform/spaces/sp-shared/members': { body: { members: [ALICE_OWNER, BOB_VIEWER] } },
      'PATCH /api/platform/spaces/sp-shared/members/u2': (body) => ({ body: { ...BOB_VIEWER, ...(body as object) } }),
      'DELETE /api/platform/spaces/sp-shared/members/u2': { status: 204 },
    });
    renderer = await render(SpaceDetailScreen);

    await press(renderer, 'member-role-bob-editor');
    expect(requestsTo(fetchMock, 'PATCH', '/members/u2')).toEqual([{ role: 'editor' }]);
    await press(renderer, 'member-remove-bob');
    expect(requestsTo(fetchMock, 'DELETE', '/members/u2')).toHaveLength(1);
    expect(mockBack).not.toHaveBeenCalled();
  });

  it('shows last_owner when demoting the only owner', async () => {
    mockFetchRoutes({
      'GET /api/platform/spaces/sp-shared': { body: space({}) },
      'GET /api/platform/spaces/sp-shared/members': { body: { members: [ALICE_OWNER] } },
      'PATCH /api/platform/spaces/sp-shared/members/u1': { status: 409, body: { detail: 'last_owner' } },
    });
    renderer = await render(SpaceDetailScreen);
    await press(renderer, 'member-role-alice-viewer');

    expect(textOf(renderer)).toContain('A space needs at least one owner.');
  });

  it('a non-owner sees members and roles but no controls', async () => {
    mockFetchRoutes({
      'GET /api/platform/spaces/sp-shared': { body: space({ role: 'viewer' }) },
      'GET /api/platform/spaces/sp-shared/members': { body: { members: [ALICE_OWNER, BOB_VIEWER] } },
    });
    renderer = await render(SpaceDetailScreen);

    expect(exists(renderer, 'member-row-bob')).toBe(true);
    expect(exists(renderer, 'member-role-bob')).toBe(true);
    expect(exists(renderer, 'member-remove-bob')).toBe(false);
    expect(exists(renderer, 'space-add-member')).toBe(false);
  });

  it('a personal space offers no sharing', async () => {
    mockSpaceId = 'sp-personal';
    mockFetchRoutes({
      'GET /api/platform/spaces/sp-personal': { body: PERSONAL },
      'GET /api/platform/spaces/sp-personal/members': { body: { members: [ALICE_OWNER] } },
    });
    renderer = await render(SpaceDetailScreen);

    expect(textOf(renderer)).toContain('Only you can see it.');
    expect(exists(renderer, 'space-add-member')).toBe(false);
    expect(exists(renderer, 'member-remove-alice')).toBe(false);
  });

  it('a missing space shows the error with retry', async () => {
    mockFetchRoutes({
      'GET /api/platform/spaces/sp-shared': { status: 404, body: { detail: 'not_found' } },
      'GET /api/platform/spaces/sp-shared/members': { status: 404, body: { detail: 'not_found' } },
    });
    renderer = await render(SpaceDetailScreen);

    expect(textOf(renderer)).toContain('Not found.');
    expect(exists(renderer, 'settings-load-retry')).toBe(true);
  });
});
