// M9-04: react-native-keyboard-controller is a native module (Reanimated
// peer). Jest never loads the real one — the mock is a passthrough so
// existing screen tests keep rendering without the native keyboard stack.
jest.mock('react-native-keyboard-controller', () => {
  const { KeyboardAvoidingView, View } = require('react-native');
  return {
    KeyboardProvider: ({ children }) => children,
    KeyboardAvoidingView,
    KeyboardStickyView: View,
  };
});

jest.mock('expo-local-authentication', () => ({
  hasHardwareAsync: jest.fn(async () => true),
  authenticateAsync: jest.fn(async () => ({ success: true })),
}));

jest.mock('homeai-device-key', () => ({
  generateKey: jest.fn(),
  sign: jest.fn(),
  publicKey: jest.fn(),
  hasKey: jest.fn(),
  deleteKey: jest.fn(),
}));

// M19-02: the chat drawer pads by the safe-area insets; tests have no provider.
jest.mock('react-native-safe-area-context', () => require('react-native-safe-area-context/jest/mock').default);
