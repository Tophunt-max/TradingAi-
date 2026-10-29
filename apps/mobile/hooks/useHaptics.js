import { useCallback } from 'react';
import { Platform } from 'react-native';
import * as Haptics from 'expo-haptics';
import { usePreferencesStore } from '../store/preferences';

// Haptics are a no-op on web and when the user disabled them.
export function useHaptics() {
  const enabled = usePreferencesStore((s) => s.hapticsEnabled);
  const active = enabled && Platform.OS !== 'web';

  const tap = useCallback(() => {
    if (active) Haptics.selectionAsync().catch(() => {});
  }, [active]);

  const notify = useCallback(
    (type = 'success') => {
      if (!active) return;
      const map = {
        success: Haptics.NotificationFeedbackType.Success,
        warning: Haptics.NotificationFeedbackType.Warning,
        error: Haptics.NotificationFeedbackType.Error,
      };
      Haptics.notificationAsync(map[type] ?? map.success).catch(() => {});
    },
    [active],
  );

  return { tap, notify };
}
