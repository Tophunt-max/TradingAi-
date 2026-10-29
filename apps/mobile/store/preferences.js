import { create } from 'zustand';

// Local mirror of server-side preferences that affect rendering.
export const usePreferencesStore = create((set) => ({
  theme: 'dark',
  language: 'en',
  reducedMotion: false,
  hapticsEnabled: true,
  hydrate: (prefs) =>
    set({
      theme: prefs.theme ?? 'dark',
      language: prefs.language ?? 'en',
      reducedMotion: !!prefs.reduced_motion,
      hapticsEnabled: prefs.haptics_enabled !== false,
    }),
}));
