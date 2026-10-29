import Constants from 'expo-constants';
import { Platform } from 'react-native';

// Only public, non-secret configuration belongs here. Broker credentials and
// API secrets must never be shipped in the client bundle.
function defaultApiUrl() {
  // Android emulator reaches the host machine via 10.0.2.2.
  if (Platform.OS === 'android' && !Constants.isDevice) return 'http://10.0.2.2:8000';
  return 'http://localhost:8000';
}

export const API_URL = (process.env.EXPO_PUBLIC_API_URL || defaultApiUrl()).replace(/\/$/, '');
export const REQUEST_TIMEOUT_MS = 15000;
