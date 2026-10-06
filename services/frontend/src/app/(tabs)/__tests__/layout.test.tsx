import { createElement, type ReactNode } from 'react';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';

type ScreenProps = { name: string; options?: { headerShown?: boolean; title?: string; headerLeft?: () => ReactNode } };

const mockNavigate = jest.fn();
const mockDismissTo = jest.fn();
const mockScreens: ScreenProps[] = [];
const mockRedirects: string[] = [];

jest.mock('expo-router', () => {
  const Stack = (props: { children: ReactNode }) => props.children;
  Stack.Screen = function Screen(props: ScreenProps) {
    mockScreens.push(props);
    return null;
  };
  const Redirect = ({ href }: { href: string }) => {
    mockRedirects.push(href);
    return null;
  };
  return { Stack, Redirect, useRouter: () => ({ navigate: mockNavigate, dismissTo: mockDismissTo }) };
});

const mockShowPage = jest.fn();
jest.mock('@/lib/currentPage', () => ({ showPage: (...args: unknown[]) => mockShowPage(...args) }));

// eslint-disable-next-line import/first -- must follow the jest.mock calls above
import ShellLayout, { unstable_settings } from '../_layout';
// eslint-disable-next-line import/first -- must follow the jest.mock calls above
import AppsLink from '../apps/index';
// eslint-disable-next-line import/first -- must follow the jest.mock calls above
import ChatLink from '../chat/index';
// eslint-disable-next-line import/first -- must follow the jest.mock calls above
import SettingsRoutinesRedirect from '../settings/routines';

let renderer: ReactTestRenderer | null = null;
afterEach(() => {
  act(() => renderer?.unmount());
  renderer = null;
  mockScreens.length = 0;
  jest.clearAllMocks();
});

describe('ShellLayout (M19-07)', () => {
  it('is a stack over the home pager; deep links keep the pager underneath', () => {
    act(() => {
      renderer = create(createElement(ShellLayout));
    });
    expect(mockScreens.map((s) => s.name)).toEqual(['index', 'chat', 'apps', 'files', 'routines', 'settings']);
    expect(unstable_settings.initialRouteName).toBe('index');
  });

  it("Files' back button returns to the home pager", () => {
    act(() => {
      renderer = create(createElement(ShellLayout));
    });
    const files = mockScreens.find((s) => s.name === 'files');
    let back!: ReactTestRenderer;
    act(() => {
      back = create(files?.options?.headerLeft?.() as React.ReactElement);
    });
    act(() => back.root.findByProps({ testID: 'back-to-apps' }).props.onPress());
    expect(mockNavigate).toHaveBeenCalledWith('/');
    act(() => back.unmount());
  });

  it('/chat and /apps show their page of the pager; the old Settings → Routines link opens Routines', () => {
    act(() => {
      renderer = create(createElement(ChatLink));
    });
    expect(mockShowPage).toHaveBeenLastCalledWith('chat');
    expect(mockDismissTo).toHaveBeenLastCalledWith('/');
    act(() => renderer?.update(createElement(AppsLink)));
    expect(mockShowPage).toHaveBeenLastCalledWith('apps');
    act(() => renderer?.update(createElement(SettingsRoutinesRedirect)));
    expect(mockRedirects).toEqual(['/routines']);
  });
});
