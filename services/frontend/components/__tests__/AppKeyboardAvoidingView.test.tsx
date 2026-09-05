import { createElement } from 'react';
import { Text } from 'react-native';
import { act, create } from 'react-test-renderer';

import { AppKeyboardAvoidingView } from '../AppKeyboardAvoidingView';
import { AppKeyboardProvider } from '../AppKeyboardProvider';

describe('AppKeyboardAvoidingView / AppKeyboardProvider', () => {
  it('renders children through the mocked keyboard-controller wrappers', () => {
    let renderer: ReturnType<typeof create> | undefined;
    act(() => {
      renderer = create(
        createElement(
          AppKeyboardProvider,
          null,
          createElement(AppKeyboardAvoidingView, null, createElement(Text, null, 'composer')),
        ),
      );
    });

    expect(JSON.stringify(renderer!.toJSON())).toContain('composer');
  });
});
