import { type ReactNode } from 'react';
import { type StyleProp, type ViewStyle } from 'react-native';
import { KeyboardAvoidingView } from 'react-native-keyboard-controller';

/**
 * M9-04: native replacement for RN's `KeyboardAvoidingView`. `padding`
 * shrinks the screen when the keyboard opens so a bottom-pinned chat list
 * (and a centered PromptModal card) stay above it. Web stays on RN's own
 * KAV — see `AppKeyboardAvoidingView.web.tsx`.
 */
export function AppKeyboardAvoidingView({
  children,
  style,
}: {
  children: ReactNode;
  style?: StyleProp<ViewStyle>;
}) {
  return (
    <KeyboardAvoidingView style={style} behavior="padding">
      {children}
    </KeyboardAvoidingView>
  );
}
