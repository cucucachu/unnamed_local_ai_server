import { createElement, type ReactNode } from 'react';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';

type ScreenProps = { name: string; options?: { href?: null; title?: string; headerLeft?: () => ReactNode } };
type TabsProps = { backBehavior?: string; children: ReactNode };

const mockNavigate = jest.fn();
const mockScreens: ScreenProps[] = [];
let mockTabsProps: TabsProps | null = null;
const mockRedirects: string[] = [];

jest.mock('expo-router', () => {
  const Tabs = (props: TabsProps) => {
    mockTabsProps = props;
    return props.children;
  };
  Tabs.Screen = function Screen(props: ScreenProps) {
    mockScreens.push(props);
    return null;
  };
  const Redirect = ({ href }: { href: string }) => {
    mockRedirects.push(href);
    return null;
  };
  return { Tabs, Redirect, useRouter: () => ({ navigate: mockNavigate }) };
});

jest.mock('@/lib/chatAttention', () => ({ useChatAttention: () => 0 }));

// eslint-disable-next-line import/first -- must follow the jest.mock calls above
import TabsLayout from '../_layout';
// eslint-disable-next-line import/first -- must follow the jest.mock calls above
import TabsIndex from '../index';
// eslint-disable-next-line import/first -- must follow the jest.mock calls above
import SettingsRoutinesRedirect from '../settings/routines';

let renderer: ReactTestRenderer | null = null;
afterEach(() => {
  act(() => renderer?.unmount());
  renderer = null;
  mockScreens.length = 0;
});

describe('TabsLayout (M19-01)', () => {
  it('shows only Chat and Apps; Files, Routines and Settings are hidden; back follows history', () => {
    act(() => {
      renderer = create(createElement(TabsLayout));
    });
    const visible = mockScreens.filter((s) => s.options?.href !== null).map((s) => s.name);
    expect(visible).toEqual(['chat', 'apps']);
    const hidden = mockScreens.filter((s) => s.options?.href === null).map((s) => s.name);
    expect(hidden).toEqual(expect.arrayContaining(['index', 'files', 'routines', 'settings']));
    expect(mockTabsProps?.backBehavior).toBe('history');
  });

  it("Files' back button returns to Apps", () => {
    act(() => {
      renderer = create(createElement(TabsLayout));
    });
    const files = mockScreens.find((s) => s.name === 'files');
    let back!: ReactTestRenderer;
    act(() => {
      back = create(files?.options?.headerLeft?.() as React.ReactElement);
    });
    act(() => back.root.findByProps({ testID: 'back-to-apps' }).props.onPress());
    expect(mockNavigate).toHaveBeenCalledWith('/apps');
    act(() => back.unmount());
  });

  it('/ opens Chat and the old Settings → Routines link opens Routines', () => {
    act(() => {
      renderer = create(createElement(TabsIndex));
    });
    act(() => renderer?.update(createElement(SettingsRoutinesRedirect)));
    expect(mockRedirects).toEqual(['/chat', '/routines']);
  });
});
