import { createElement } from 'react';
import * as ReactNative from 'react-native';
import { act, create } from 'react-test-renderer';

import { palettes, themedStyles, useTheme } from '@/lib/theme';

const useStyles = themedStyles((theme) => ({ box: { backgroundColor: theme.bg } }));

function Probe({ seen }: { seen: unknown[] }) {
  seen.push([useTheme().scheme, useStyles()]);
  return null;
}

describe('theme', () => {
  it('follows the device scheme and builds each scheme’s styles once', () => {
    const scheme = jest.spyOn(ReactNative, 'useColorScheme');
    const seen: [string, { box: { backgroundColor: string } }][] = [];

    scheme.mockReturnValue('light');
    let renderer!: ReturnType<typeof create>;
    act(() => {
      renderer = create(createElement(Probe, { seen }));
    });
    scheme.mockReturnValue('dark');
    act(() => renderer.update(createElement(Probe, { seen })));
    act(() => renderer.update(createElement(Probe, { seen })));
    scheme.mockReturnValue('unspecified');
    act(() => renderer.update(createElement(Probe, { seen })));

    expect(seen.map(([s]) => s)).toEqual(['light', 'dark', 'dark', 'dark']);
    expect(seen[0][1].box.backgroundColor).toBe(palettes.light.bg);
    expect(seen[1][1].box.backgroundColor).toBe(palettes.dark.bg);
    expect(seen[2][1]).toBe(seen[1][1]);
    scheme.mockRestore();
  });

  it('has the same tokens in both palettes', () => {
    expect(Object.keys(palettes.light).sort()).toEqual(Object.keys(palettes.dark).sort());
  });
});
