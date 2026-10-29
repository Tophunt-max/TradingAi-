import { Platform } from 'react-native';
import * as SecureStore from 'expo-secure-store';

/**
 * Secure token storage.
 * - iOS/Android: Keychain / Keystore via expo-secure-store.
 * - Web: refresh tokens are kept in memory only (never localStorage), so a page
 *   reload requires signing in again. This avoids exposing long-lived tokens to XSS.
 */
const memory = new Map();
const isWeb = Platform.OS === 'web';

export const secureStorage = {
  async get(key) {
    if (isWeb) return memory.get(key) ?? null;
    return SecureStore.getItemAsync(key);
  },
  async set(key, value) {
    if (isWeb) {
      memory.set(key, value);
      return;
    }
    await SecureStore.setItemAsync(key, value, { keychainAccessible: SecureStore.WHEN_UNLOCKED_THIS_DEVICE_ONLY });
  },
  async remove(key) {
    if (isWeb) {
      memory.delete(key);
      return;
    }
    await SecureStore.deleteItemAsync(key);
  },
};

/** Non-sensitive UI flags (e.g. onboarding seen). */
export const flagStorage = {
  async get(key) {
    if (isWeb) {
      try {
        return globalThis.localStorage?.getItem(key) ?? null;
      } catch {
        return null;
      }
    }
    return SecureStore.getItemAsync(key);
  },
  async set(key, value) {
    if (isWeb) {
      try {
        globalThis.localStorage?.setItem(key, value);
      } catch {
        // storage unavailable (private mode); ignore
      }
      return;
    }
    await SecureStore.setItemAsync(key, value);
  },
};
