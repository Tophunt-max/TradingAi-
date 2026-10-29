import { createContext, useContext, useMemo, useState } from 'react';
import { useColorScheme } from 'react-native';
import { buildTheme } from '../../constants/theme';

const ThemeContext = createContext({ theme: buildTheme('dark'), preference: 'dark', setPreference: () => {} });

export function ThemeProvider({ children, initialPreference = 'dark' }) {
  const system = useColorScheme();
  const [preference, setPreference] = useState(initialPreference);
  const mode = preference === 'system' ? (system === 'light' ? 'light' : 'dark') : preference;
  const value = useMemo(() => ({ theme: buildTheme(mode), preference, setPreference }), [mode, preference]);
  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme() {
  return useContext(ThemeContext).theme;
}

export function useThemePreference() {
  const { preference, setPreference } = useContext(ThemeContext);
  return { preference, setPreference };
}
