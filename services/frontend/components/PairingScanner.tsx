import { CameraView, useCameraPermissions } from 'expo-camera';
import { useEffect, useRef } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';

import { theme } from '@/lib/theme';

/** Camera view that reads one QR code (the Settings pairing QR) and hands
 * its text to `onScanned`. Asks for camera permission on first open. */
export function PairingScanner({
  onScanned,
  onCancel,
}: {
  onScanned: (data: string) => void;
  onCancel: () => void;
}) {
  const [permission, requestPermission] = useCameraPermissions();
  const done = useRef(false);

  useEffect(() => {
    if (permission && !permission.granted && permission.canAskAgain) {
      requestPermission();
    }
  }, [permission, requestPermission]);

  return (
    <View style={styles.wrap} testID="host-pair-scanner">
      {permission?.granted ? (
        <CameraView
          style={styles.camera}
          facing="back"
          barcodeScannerSettings={{ barcodeTypes: ['qr'] }}
          onBarcodeScanned={({ data }) => {
            if (done.current) return;
            done.current = true;
            onScanned(data);
          }}
          testID="host-pair-camera"
        />
      ) : (
        <Text style={styles.message} testID="host-pair-camera-denied">
          {permission && !permission.canAskAgain
            ? 'Camera access is off for Home AI. Turn it on in Android settings, or paste the pairing code.'
            : 'Waiting for camera permission…'}
        </Text>
      )}
      <Pressable onPress={onCancel} accessibilityRole="button" testID="host-pair-scan-cancel" style={styles.cancel}>
        <Text style={styles.cancelText}>Cancel</Text>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: {
    gap: 8,
  },
  camera: {
    height: 280,
    borderRadius: 8,
    overflow: 'hidden',
  },
  message: {
    color: theme.textMuted,
    fontSize: 14,
  },
  cancel: {
    alignSelf: 'center',
    paddingVertical: 8,
    paddingHorizontal: 16,
  },
  cancelText: {
    color: theme.accent,
    fontSize: 15,
  },
});
