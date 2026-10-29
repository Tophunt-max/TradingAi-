import { create } from 'zustand';
import { createApiClient } from '../services/api';
import { flagStorage, secureStorage } from '../services/storage';

const REFRESH_KEY = 'quantai.refresh_token';
const ONBOARDING_KEY = 'quantai.onboarding_seen';

// Access token lives in memory only; the refresh token is in secure storage.
let accessToken = null;

export const useAuthStore = create((set, get) => ({
  status: 'loading', // loading | signedOut | signedIn
  user: null,
  onboardingSeen: false,

  async bootstrap() {
    const seen = (await flagStorage.get(ONBOARDING_KEY)) === '1';
    set({ onboardingSeen: seen });
    const refresh = await secureStorage.get(REFRESH_KEY);
    if (!refresh) {
      set({ status: 'signedOut' });
      return;
    }
    try {
      const me = await api.get('/api/v1/users/me'); // triggers refresh via 401
      set({ status: 'signedIn', user: me });
    } catch {
      await get().clearSession();
    }
  },

  async markOnboardingSeen() {
    await flagStorage.set(ONBOARDING_KEY, '1');
    set({ onboardingSeen: true });
  },

  async applyTokens(pair) {
    accessToken = pair.access_token;
    await secureStorage.set(REFRESH_KEY, pair.refresh_token);
    set({ status: 'signedIn', user: pair.user });
  },

  async clearSession() {
    accessToken = null;
    await secureStorage.remove(REFRESH_KEY);
    set({ status: 'signedOut', user: null });
  },

  setUser(user) {
    set({ user });
  },

  async logout() {
    try {
      await api.post('/api/v1/auth/logout');
    } catch {
      // Server-side revoke failed (offline); still clear local credentials.
    }
    await get().clearSession();
  },
}));

export const api = createApiClient({
  tokens: {
    getAccess: () => accessToken,
    getRefresh: () => secureStorage.get(REFRESH_KEY),
    setTokens: async (pair) => {
      accessToken = pair.access_token;
      await secureStorage.set(REFRESH_KEY, pair.refresh_token);
      useAuthStore.setState({ user: pair.user });
    },
    onSessionExpired: () => useAuthStore.getState().clearSession(),
  },
});
