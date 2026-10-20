import { createElement } from 'react';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';

import { AppGrid, slotAt } from '@/components/AppGrid';
import { SpacePage, type SpacePageProps } from '@/components/SpacePage';
import { arrangeTiles, type Instance } from '@/lib/apps';
import type { Space } from '@/lib/platform';

jest.mock('expo-router', () => ({ useRouter: () => ({ push: jest.fn() }) }));

const SPACE = { id: 's1', slug: 'family', name: 'Family', kind: 'shared', role: 'owner', archived_at: null } as unknown as Space;

function instance(id: string, slug: string): Instance {
  return {
    id,
    app_id: `app-${id}`,
    space_id: SPACE.id,
    tracks: 'working',
    installed_by: null,
    granted_permissions: {},
    created_at: '2026-10-01T00:00:00Z',
    app: { id: `app-${id}`, slug, name: slug, version: null, icon: null },
    update: null,
  };
}

function render(props: Partial<SpacePageProps> = {}): ReactTestRenderer {
  let renderer!: ReactTestRenderer;
  act(() => {
    renderer = create(
      createElement(SpacePage, {
        space: SPACE,
        instances: [instance('i1', 'notes'), instance('i2', 'chores')],
        order: [],
        width: 400,
        arranging: false,
        onRefresh: jest.fn(),
        onLongPressApp: jest.fn(),
        onArrange: jest.fn(),
        onDoneArranging: jest.fn(),
        onSaveOrder: jest.fn(),
        ...props,
      }),
    );
  });
  return renderer;
}

const cellKeys = (renderer: ReactTestRenderer) =>
  renderer.root
    .findAll((node) => typeof node.props.testID === 'string' && node.props.testID.startsWith('app-grid-cell-'))
    .filter((node) => typeof node.type === 'string' || node.type.toString().includes('View'))
    .map((node) => node.props.testID.replace('app-grid-cell-', ''))
    .filter((key, index, all) => all.indexOf(key) === index);

describe('arrangeTiles', () => {
  it('puts saved tiles first, drops gone ones, and appends new ones', () => {
    expect(arrangeTiles(['files', 'a', 'b', 'c'], ['c', 'gone', 'files'])).toEqual(['c', 'files', 'a', 'b']);
    expect(arrangeTiles(['files', 'a'], [])).toEqual(['files', 'a']);
  });
});

describe('slotAt', () => {
  const grid = { width: 400, height: 200 };
  it('counts from the bottom-right, leftward then upward', () => {
    expect(slotAt(390, 190, grid, 84, 4)).toBe(0);
    expect(slotAt(10, 190, grid, 84, 4)).toBe(3);
    expect(slotAt(390, 10, grid, 84, 4)).toBe(4);
    expect(slotAt(-50, 500, grid, 84, 4)).toBe(3);
  });
});

describe('SpacePage', () => {
  it('lays tiles out in the saved order with Add app last', () => {
    const renderer = render({ order: ['i2', 'files'] });
    expect(cellKeys(renderer)).toEqual(['i2', 'files', 'i1', 'add']);
  });

  it('saves the order a drag ends with, without Add app', () => {
    const onSaveOrder = jest.fn();
    const renderer = render({ arranging: true, onSaveOrder });
    expect(renderer.root.findByProps({ testID: 'apps-arrange-done' })).toBeTruthy();
    const grid = renderer.root.findByType(AppGrid);
    act(() => grid.props.onArrange(['i1', 'files', 'i2', 'add'], false));
    expect(cellKeys(renderer)).toEqual(['i1', 'files', 'i2', 'add']);
    expect(onSaveOrder).not.toHaveBeenCalled();
    act(() => grid.props.onArrange(['i1', 'files', 'i2', 'add'], true));
    expect(onSaveOrder).toHaveBeenCalledWith(SPACE, ['i1', 'files', 'i2']);
  });

  it('long-pressing a built-in tile starts arranging', () => {
    const onArrange = jest.fn();
    const renderer = render({ onArrange });
    act(() => renderer.root.findByProps({ testID: 'home-open-files' }).props.onLongPress());
    expect(onArrange).toHaveBeenCalledWith(SPACE);
  });
});
