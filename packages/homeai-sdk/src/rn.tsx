// What apps get as `react-native`: react-native-web, with Text and TextInput
// defaulting to the theme's text colours when the app is dark (light apps
// render exactly as before), Pressable's onPressIn on touch as on a phone,
// and useColorScheme answering for the app.
import { createContext, forwardRef, useContext, type ComponentProps, type ElementRef } from 'react';
import * as RN from 'react-native';
import { useColorScheme, useTheme } from './theme';

// Nested Text inherits its parent's colour, so only the outermost one gets the default.
const InsideText = createContext(false);

export const Text = forwardRef<ElementRef<typeof RN.Text>, ComponentProps<typeof RN.Text>>(function Text(props, ref) {
  const inside = useContext(InsideText);
  const t = useTheme();
  const style = inside || t.scheme === 'light' ? props.style : [{ color: t.colors.text }, props.style];
  const el = <RN.Text ref={ref} {...props} style={style} />;
  return inside ? el : <InsideText.Provider value>{el}</InsideText.Provider>;
});

export const TextInput = forwardRef<ElementRef<typeof RN.TextInput>, ComponentProps<typeof RN.TextInput>>(function TextInput(props, ref) {
  const t = useTheme();
  if (t.scheme === 'light') return <RN.TextInput ref={ref} {...props} />;
  return (
    <RN.TextInput
      ref={ref}
      placeholderTextColor={t.colors.textMuted}
      {...props}
      style={[{ color: t.colors.text }, props.style]}
    />
  );
});

// react-native-web holds onPressIn back 50 ms, and drops it for a quicker tap without onPress;
// React Native calls it on touch.
export const Pressable = forwardRef<ElementRef<typeof RN.Pressable>, ComponentProps<typeof RN.Pressable>>(function Pressable(props, ref) {
  return <RN.Pressable ref={ref} {...({ delayPressIn: 0 } as object)} {...props} />;
});

export const reactNative = { ...RN, Text, TextInput, Pressable, useColorScheme };
