import { createElement, type ComponentType } from 'react';
import { Text as RNText } from 'react-native';
import { act, create, type ReactTestInstance, type ReactTestRenderer } from 'react-test-renderer';

/** Helpers for the Settings screen suites: a `fetch` mock routed by
 * `"METHOD /path"`, and thin wrappers over `react-test-renderer`. */

export type RouteReply = { status?: number; body?: unknown };
export type Route = RouteReply | ((body: unknown) => RouteReply);

export function mockFetchRoutes(routes: Record<string, Route>): jest.Mock {
  const mock = jest.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET';
    const path = String(url).replace(/^https?:\/\/[^/]+/, '');
    const route = routes[`${method} ${path}`];
    if (route === undefined) throw new Error(`unexpected request ${method} ${path}`);
    const reply = typeof route === 'function' ? route(init?.body ? JSON.parse(String(init.body)) : undefined) : route;
    const status = reply.status ?? 200;
    return {
      ok: status < 300,
      status,
      statusText: '',
      headers: { get: () => null },
      json: async () => {
        if (reply.body === undefined) throw new SyntaxError('no body');
        return reply.body;
      },
    };
  });
  global.fetch = mock as unknown as typeof fetch;
  return mock;
}

export function requestsTo(mock: jest.Mock, method: string, path: string): unknown[] {
  return mock.mock.calls
    .filter(([url, init]) => (init?.method ?? 'GET') === method && String(url).endsWith(path))
    .map(([, init]) => (init?.body ? JSON.parse(String(init.body)) : undefined));
}

export async function flush(): Promise<void> {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

export async function render(Component: ComponentType): Promise<ReactTestRenderer> {
  let renderer!: ReactTestRenderer;
  await act(async () => {
    renderer = create(createElement(Component));
  });
  await flush();
  return renderer;
}

export function exists(renderer: ReactTestRenderer, testID: string): boolean {
  return renderer.root.findAll((node) => node.props.testID === testID).length > 0;
}

function interactive(renderer: ReactTestRenderer, testID: string, prop: string): ReactTestInstance {
  return renderer.root.find((node) => node.props.testID === testID && typeof node.props[prop] === 'function');
}

export async function press(renderer: ReactTestRenderer, testID: string): Promise<void> {
  await act(async () => {
    interactive(renderer, testID, 'onPress').props.onPress();
  });
  await flush();
}

export function isDisabled(renderer: ReactTestRenderer, testID: string): boolean {
  return Boolean(interactive(renderer, testID, 'onPress').props.disabled);
}

export async function type(renderer: ReactTestRenderer, testID: string, value: string): Promise<void> {
  await act(async () => {
    interactive(renderer, testID, 'onChangeText').props.onChangeText(value);
  });
}

export function textOf(renderer: ReactTestRenderer): string {
  return renderer.root
    .findAllByType(RNText)
    .map((node) => {
      const children = node.props.children;
      return Array.isArray(children) ? children.join('') : String(children ?? '');
    })
    .join(' | ');
}
