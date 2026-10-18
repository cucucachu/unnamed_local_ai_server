// `Ionicons` from @expo/vector-icons, over the subset font in icons.generated.ts
// (scripts/icons.py). Names outside the subset are a type error in app code
// and render nothing.
import { Text, type StyleProp, type TextStyle } from 'react-native';
import { ICON_CODES, ICON_FONT } from './icons.generated';

let fontAdded = false;

function addFont() {
  if (fontAdded || typeof document === 'undefined') return;
  fontAdded = true;
  const style = document.createElement('style');
  style.textContent = `@font-face{font-family:Ionicons;src:url(${ICON_FONT}) format('woff2');font-display:block}`;
  document.head.appendChild(style);
}

export function Ionicons({ name, size = 24, color = '#000', style, testID }: {
  name: string;
  size?: number;
  color?: string;
  style?: StyleProp<TextStyle>;
  testID?: string;
}) {
  addFont();
  const code = ICON_CODES[name];
  if (code === undefined) return null;
  return (
    <Text
      testID={testID}
      aria-hidden
      selectable={false}
      style={[{ fontFamily: 'Ionicons', fontSize: size, lineHeight: size, color, fontWeight: 'normal', fontStyle: 'normal' }, style]}
    >
      {String.fromCodePoint(code)}
    </Text>
  );
}

export const iconNames = () => Object.keys(ICON_CODES);
