import { Platform } from 'react-native';
import { Tabs } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import { useTheme } from '../../../components/theme/ThemeProvider';

const TABS = [
  { name: 'index', title: 'Home', icon: 'home' },
  { name: 'markets', title: 'Markets', icon: 'trending-up' },
  { name: 'ai-lab', title: 'AI Lab', icon: 'sparkles' },
  { name: 'bots', title: 'Bots', icon: 'hardware-chip' },
  { name: 'portfolio', title: 'Portfolio', icon: 'wallet' },
];

export default function TabsLayout() {
  const theme = useTheme();
  return (
    <Tabs
      screenOptions={{
        headerShown: false,
        tabBarActiveTintColor: theme.colors.primary,
        tabBarInactiveTintColor: theme.colors.textFaint,
        tabBarStyle: {
          backgroundColor: theme.colors.backgroundDeep,
          borderTopColor: theme.colors.borderSubtle,
          // Web has no safe-area inset, so give labels explicit room.
          ...(Platform.OS === 'web' ? { height: 68, paddingBottom: 10, paddingTop: 6 } : null),
        },
        tabBarLabelStyle: { fontSize: 12, fontWeight: '600' },
      }}
    >
      {TABS.map((t) => (
        <Tabs.Screen
          key={t.name}
          name={t.name}
          options={{
            title: t.title,
            tabBarAccessibilityLabel: t.title,
            tabBarIcon: ({ color, focused, size }) => (
              <Ionicons name={focused ? t.icon : `${t.icon}-outline`} color={color} size={size ?? 22} />
            ),
          }}
        />
      ))}
    </Tabs>
  );
}
