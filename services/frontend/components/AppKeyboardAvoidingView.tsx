import { type ReactNode } from 'react';
import { type StyleProp, type ViewStyle } from 'react-native';
import { KeyboardAvoidingView } from 'react-native-keyboard-controller';

/**
 * M9-04: native replacement for RN's `KeyboardAvoidingView`. `padding`
 * shrinks the screen when the keyboard opens so a bottom-pinned chat list
 * (and a centered PromptModal card) stay above it. Web stays on RN's own
 * KAV — see `AppKeyboardAvoidingView.web.tsx`.
 *
 * `automaticOffset`: without it the library takes the view's top from
 * `onLayout`, which is relative to the parent, so under a stack header
 * (plus the edge-to-edge status bar) it under-pads by that height and the
 * composer stays behind the keyboard.
 */
export function AppKeyboardAvoidingView({
  children,
  style,
}: {
  children: ReactNode;
  style?: StyleProp<ViewStyle>;
}) {
  return (
    <KeyboardAvoidingView style={style} behavior="padding" automaticOffset>
      {children}
    </KeyboardAvoidingView>
  );
}
