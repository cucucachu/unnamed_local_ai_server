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
