import { ImageManipulator, SaveFormat } from 'expo-image-manipulator';
import * as ImagePicker from 'expo-image-picker';
import { Platform } from 'react-native';

/** What the platform stores per photo (`app/core/appimages.py`), so it still fits the bridge as a data URL. */
export const MAX_PHOTO_BYTES = 512 * 1024;
/** Tried in order until the JPEG is small enough. */
const ATTEMPTS = [
  { edge: 1280, compress: 0.8 },
  { edge: 1280, compress: 0.6 },
  { edge: 960, compress: 0.6 },
  { edge: 720, compress: 0.5 },
];

export type PickedPhoto = { base64: string; width: number; height: number };

function photoError(code: string, message: string) {
  return Object.assign(new Error(message), { code });
}

export function fitWithin(width: number, height: number, edge: number) {
  const scale = Math.min(1, edge / Math.max(width, height, 1));
  return { width: Math.max(1, Math.round(width * scale)), height: Math.max(1, Math.round(height * scale)) };
}

export function base64Bytes(base64: string) {
  const padding = base64.endsWith('==') ? 2 : base64.endsWith('=') ? 1 : 0;
  return (base64.length * 3) / 4 - padding;
}

/**
 * The user takes or chooses a photo, shrunk to a JPEG of at most 1280 px and `MAX_PHOTO_BYTES`;
 * null if they cancel. The web uses the browser's file input (and its camera capture).
 */
export async function pickPhoto(source: 'camera' | 'library'): Promise<PickedPhoto | null> {
  if (source === 'camera' && Platform.OS !== 'web') {
    const permission = await ImagePicker.requestCameraPermissionsAsync();
    if (!permission.granted) throw photoError('permission_denied', 'Camera access is off for Home AI. Turn it on in Settings.');
  }
  const options: ImagePicker.ImagePickerOptions = { mediaTypes: ['images'], quality: 1, exif: false };
  const result =
    source === 'camera' ? await ImagePicker.launchCameraAsync(options) : await ImagePicker.launchImageLibraryAsync(options);
  if (result.canceled || !result.assets?.length) return null;
  const asset = result.assets[0];
  for (const { edge, compress } of ATTEMPTS) {
    const context = ImageManipulator.manipulate(asset.uri);
    if (asset.width && asset.height && Math.max(asset.width, asset.height) > edge) {
      context.resize(fitWithin(asset.width, asset.height, edge));
    }
    const image = await context.renderAsync();
    const saved = await image.saveAsync({ format: SaveFormat.JPEG, compress, base64: true });
    const base64 = saved.base64?.replace(/^data:[^,]*,/, '');
    if (base64 && base64Bytes(base64) <= MAX_PHOTO_BYTES) return { base64, width: saved.width, height: saved.height };
  }
  throw photoError('image_too_large', 'That photo is too large to store, even shrunk.');
}
