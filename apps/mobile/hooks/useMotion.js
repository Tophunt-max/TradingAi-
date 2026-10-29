import { useReducedMotion } from 'react-native-reanimated';
import { usePreferencesStore } from '../store/preferences';

// True when either the OS or the in-app preference asks for reduced motion.
export function useShouldReduceMotion() {
  const system = useReducedMotion();
  const pref = usePreferencesStore((s) => s.reducedMotion);
  return system || pref;
}
