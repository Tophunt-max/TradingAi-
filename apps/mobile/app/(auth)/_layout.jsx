import { Stack } from 'expo-router';
import { useTheme } from '../../components/theme/ThemeProvider';

export default function AuthLayout() {
  const theme = useTheme();
  return (
    <Stack
      screenOptions={{
        headerStyle: { backgroundColor: theme.colors.background },
        headerTintColor: theme.colors.text,
        headerShadowVisible: false,
        headerTitle: '',
        headerBackButtonDisplayMode: 'minimal',
        contentStyle: { backgroundColor: theme.colors.background },
      }}
    >
      <Stack.Screen name="index" options={{ headerShown: false }} />
      <Stack.Screen name="onboarding" options={{ headerShown: false }} />
      <Stack.Screen name="welcome" options={{ headerShown: false }} />
    </Stack>
  );
}
