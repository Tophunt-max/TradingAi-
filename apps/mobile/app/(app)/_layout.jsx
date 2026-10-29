import { Stack } from 'expo-router';
import { useTheme } from '../../components/theme/ThemeProvider';
import { usePreferences } from '../../features/account/api';

export default function AppLayout() {
  const theme = useTheme();
  usePreferences(); // hydrate theme / motion / haptics preferences after sign-in
  return (
    <Stack
      screenOptions={{
        headerStyle: { backgroundColor: theme.colors.background },
        headerTintColor: theme.colors.text,
        headerTitleStyle: { color: theme.colors.text },
        headerShadowVisible: false,
        headerBackButtonDisplayMode: 'minimal',
        contentStyle: { backgroundColor: theme.colors.background },
      }}
    >
      <Stack.Screen name="(tabs)" options={{ headerShown: false }} />
      <Stack.Screen name="more" options={{ title: 'More' }} />
      <Stack.Screen name="system-health" options={{ title: 'System Health' }} />
      <Stack.Screen name="feature/[id]" options={{ title: '' }} />
      <Stack.Screen name="settings/index" options={{ title: 'Profile & Settings' }} />
      <Stack.Screen name="settings/security" options={{ title: 'Security' }} />
      <Stack.Screen name="settings/sessions" options={{ title: 'Active Sessions' }} />
      <Stack.Screen name="settings/audit" options={{ title: 'Audit History' }} />
    </Stack>
  );
}
