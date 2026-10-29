import { KeyboardAvoidingView, Platform, RefreshControl, ScrollView, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { StatusBar } from 'expo-status-bar';
import { useTheme } from '../theme/ThemeProvider';
import { useResponsive } from '../../hooks/useResponsive';
import { AppText } from './AppText';

export function Screen({
  children,
  title,
  subtitle,
  right,
  scroll = true,
  refreshing,
  onRefresh,
  edges = ['top'],
  keyboard = false,
  contentStyle,
}) {
  const theme = useTheme();
  const { contentMaxWidth } = useResponsive();
  const inner = (
    <View style={[{ width: '100%', maxWidth: contentMaxWidth, alignSelf: 'center', padding: theme.spacing.lg, gap: theme.spacing.lg }, contentStyle]}>
      {title ? (
        <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
          <View style={{ flex: 1 }}>
            <AppText variant="title" accessibilityRole="header">{title}</AppText>
            {subtitle ? <AppText variant="caption" tone="muted">{subtitle}</AppText> : null}
          </View>
          {right}
        </View>
      ) : null}
      {children}
    </View>
  );

  const body = scroll ? (
    <ScrollView
      keyboardShouldPersistTaps="handled"
      contentContainerStyle={{ paddingBottom: 32 }}
      refreshControl={onRefresh ? <RefreshControl refreshing={!!refreshing} onRefresh={onRefresh} tintColor={theme.colors.primary} /> : undefined}
    >
      {inner}
    </ScrollView>
  ) : (
    inner
  );

  return (
    <SafeAreaView edges={edges} style={{ flex: 1, backgroundColor: theme.colors.background }}>
      <StatusBar style={theme.mode === 'dark' ? 'light' : 'dark'} />
      {keyboard ? (
        <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
          {body}
        </KeyboardAvoidingView>
      ) : (
        body
      )}
    </SafeAreaView>
  );
}
