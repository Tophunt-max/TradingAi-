import { useEffect } from 'react';
import { Stack } from 'expo-router';
import * as SplashScreen from 'expo-splash-screen';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import { ThemeProvider, useTheme, useThemePreference } from '../components/theme/ThemeProvider';
import { SplashView } from '../features/auth/SplashView';
import { useAuthStore } from '../store/auth';
import { usePreferencesStore } from '../store/preferences';

SplashScreen.preventAutoHideAsync().catch(() => {});

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: (count, err) => !(err?.status >= 400 && err?.status < 500) && count < 2,
      refetchOnWindowFocus: false,
    },
  },
});

function ThemeSync() {
  const pref = usePreferencesStore((s) => s.theme);
  const { setPreference } = useThemePreference();
  useEffect(() => setPreference(pref), [pref, setPreference]);
  return null;
}

function RootNavigator() {
  const theme = useTheme();
  const status = useAuthStore((s) => s.status);
  const bootstrap = useAuthStore((s) => s.bootstrap);

  useEffect(() => {
    bootstrap().finally(() => SplashScreen.hideAsync().catch(() => {}));
  }, [bootstrap]);

  useEffect(() => {
    if (status === 'signedOut') queryClient.clear(); // never leak cached data across users
  }, [status]);

  if (status === 'loading') return <SplashView />;

  return (
    <Stack screenOptions={{ headerShown: false, contentStyle: { backgroundColor: theme.colors.background } }}>
      <Stack.Protected guard={status === 'signedIn'}>
        <Stack.Screen name="(app)" />
      </Stack.Protected>
      <Stack.Protected guard={status !== 'signedIn'}>
        <Stack.Screen name="(auth)" />
      </Stack.Protected>
    </Stack>
  );
}

export default function RootLayout() {
  return (
    <SafeAreaProvider>
      <QueryClientProvider client={queryClient}>
        <ThemeProvider>
          <ThemeSync />
          <RootNavigator />
        </ThemeProvider>
      </QueryClientProvider>
    </SafeAreaProvider>
  );
}
