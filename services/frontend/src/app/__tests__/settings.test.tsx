import { createElement } from 'react';
import { Text as RNText } from 'react-native';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';

import { ApiError } from '@/lib/api';
import type { SettingsDocument } from '@/lib/settings';
import { theme } from '@/lib/theme';

const mockBack = jest.fn();
jest.mock('expo-router', () => ({
  useRouter: () => ({ back: mockBack, push: jest.fn() }),
}));

// `useSettings()` is mocked directly (rather than rendering a real
// `SettingsProvider` + mocking `fetch`) since this suite is about the
// SCREEN's own rendering/interaction logic — `components/__tests__/
// SettingsProvider.test.tsx` already covers the optimistic-update/revert
// logic itself in isolation.
const mockUpdateSettings = jest.fn();
let mockSettingsValue: SettingsDocument | null = {
  hitl_enabled: true,
  thinking_enabled: false,
  edit_mode_default: 'truncate',
};
let mockLoading = false;
jest.mock('@/components/SettingsProvider', () => ({
  useSettings: () => ({
    settings: mockSettingsValue,
    loading: mockLoading,
    updateSettings: mockUpdateSettings,
  }),
}));

// eslint-disable-next-line import/first -- must follow the jest.mock calls above
import SettingsScreen from '../settings';

function textOf(renderer: ReactTestRenderer): string {
  return renderer.root
    .findAllByType(RNText)
    .map((node) => {
      const children = node.props.children;
      return Array.isArray(children) ? children.join('') : String(children ?? '');
    })
    .join(' | ');
}

let activeRenderer: ReactTestRenderer | null = null;

async function renderScreen(): Promise<ReactTestRenderer> {
  let renderer!: ReactTestRenderer;
  await act(async () => {
    renderer = create(createElement(SettingsScreen));
  });
  activeRenderer = renderer;
  return renderer;
}

beforeEach(() => {
  mockBack.mockReset();
  mockUpdateSettings.mockReset();
  mockUpdateSettings.mockResolvedValue(undefined);
  mockSettingsValue = { hitl_enabled: true, thinking_enabled: false, edit_mode_default: 'truncate' };
  mockLoading = false;
});

afterEach(() => {
  act(() => activeRenderer?.unmount());
  activeRenderer = null;
});

describe('SettingsScreen', () => {
  it('shows a loading spinner while settings have not loaded yet', async () => {
    mockLoading = true;
    mockSettingsValue = null;

    const renderer = await renderScreen();

    // No settings controls are rendered while still loading.
    expect(renderer.root.findAllByProps({ testID: 'settings-hitl-switch' }).length).toBe(0);
    expect(textOf(renderer)).toContain('Settings');
  });

  it('renders the current values for each control', async () => {
    mockSettingsValue = { hitl_enabled: false, thinking_enabled: true, edit_mode_default: 'fork' };

    const renderer = await renderScreen();

    const hitlSwitch = renderer.root.findByProps({ testID: 'settings-hitl-switch' });
    expect(hitlSwitch.props.value).toBe(false);

    const thinkingSwitch = renderer.root.findByProps({ testID: 'settings-thinking-switch' });
    expect(thinkingSwitch.props.value).toBe(true);

    // The selected segment ("Branch"/"fork", since `edit_mode_default: 'fork'` above) renders its
    // label in the "selected" text color (`theme.text`); the unselected one ("Replace"/"truncate")
    // renders in the muted color.
    const branchLabel = renderer.root.findByProps({ children: 'Branch' });
    const replaceLabel = renderer.root.findByProps({ children: 'Replace' });
    const flatStyle = (style: unknown) => (Array.isArray(style) ? Object.assign({}, ...style) : style);
    expect(flatStyle(branchLabel.props.style).color).toBe(theme.text);
    expect(flatStyle(replaceLabel.props.style).color).toBe(theme.textMuted);

    expect(textOf(renderer)).toContain('Require approval before the agent writes files or runs code.');
    expect(textOf(renderer)).toContain("Voice input uses your browser's speech service.");
  });

  it('toggling the HITL switch calls updateSettings with the new value', async () => {
    const renderer = await renderScreen();

    const hitlSwitch = renderer.root.findByProps({ testID: 'settings-hitl-switch' });
    await act(async () => {
      hitlSwitch.props.onValueChange(false);
    });

    expect(mockUpdateSettings).toHaveBeenCalledWith({ hitl_enabled: false });
  });

  it('tapping the "Branch" segment calls updateSettings with edit_mode_default: "fork"', async () => {
    const renderer = await renderScreen();

    const forkButton = renderer.root.findByProps({ testID: 'settings-edit-mode-fork' });
    await act(async () => {
      forkButton.props.onPress();
    });

    expect(mockUpdateSettings).toHaveBeenCalledWith({ edit_mode_default: 'fork' });
  });

  it('shows a toast when updateSettings rejects (optimistic-revert-on-failure surfaced by the provider)', async () => {
    mockUpdateSettings.mockRejectedValue(new ApiError(422, 'update failed'));

    const renderer = await renderScreen();
    const hitlSwitch = renderer.root.findByProps({ testID: 'settings-hitl-switch' });

    await act(async () => {
      hitlSwitch.props.onValueChange(false);
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    expect(textOf(renderer)).toContain('update failed');
  });

  it('the close button navigates back', async () => {
    const renderer = await renderScreen();

    const closeButton = renderer.root.findByProps({ testID: 'settings-close-button' });
    await act(async () => {
      closeButton.props.onPress();
    });

    expect(mockBack).toHaveBeenCalled();
  });
});
