import { useMemo, type ReactNode } from 'react';
import { StyleSheet, View } from 'react-native';
import { Gesture, GestureDetector } from 'react-native-gesture-handler';

const OPEN_DISTANCE = 60;

/**
 * A rightward swipe across the content opens the chat drawer (M19-02).
 * It doesn't have to start at the screen edge, which Android's back gesture
 * owns. Vertical drags fail it, so the chat still scrolls.
 */
export function SwipeToOpen({ onOpen, children }: { onOpen: () => void; children: ReactNode }) {
  const pan = useMemo(
    () =>
      Gesture.Pan()
        .runOnJS(true)
        .activeOffsetX(24)
        .failOffsetX(-12)
        .failOffsetY([-16, 16])
        .onEnd((event) => {
          if (event.translationX > OPEN_DISTANCE) onOpen();
        }),
    [onOpen],
  );
  return (
    <GestureDetector gesture={pan}>
      <View style={styles.fill}>{children}</View>
    </GestureDetector>
  );
}

const styles = StyleSheet.create({
  fill: { flex: 1 },
});
