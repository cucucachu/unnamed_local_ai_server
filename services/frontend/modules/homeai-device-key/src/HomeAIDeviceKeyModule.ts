import { NativeModule, requireNativeModule } from 'expo-modules-core';

declare class HomeAIDeviceKeyModule extends NativeModule {
  generateKey(): Promise<string>;
  sign(challengeB64url: string): Promise<string>;
  publicKey(): Promise<string | null>;
  hasKey(): Promise<boolean>;
  deleteKey(): Promise<void>;
}

export default requireNativeModule<HomeAIDeviceKeyModule>('HomeAIDeviceKey');
