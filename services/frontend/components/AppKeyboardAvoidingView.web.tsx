import { type ReactNode } from 'react';
import { KeyboardAvoidingView, type StyleProp, type ViewStyle } from 'react-native';

/**
 * Web half of the M9-04 avoiding-view split. Matches the pre-M9-04 chat
 * screen: RN's `KeyboardAvoidingView` with no `behavior` (web already
 * resizes the layout via the viewport; Android Chrome needs
 * `interactive-widget=resizes-content` in `public/index.html` for that).
 */
export function AppKeyboardAvoidingView({
  children,
  style,
}: {
  children: ReactNode;
  style?: StyleProp<ViewStyle>;
}) {
  return <KeyboardAvoidingView style={style}>{children}</KeyboardAvoidingView>;
}
