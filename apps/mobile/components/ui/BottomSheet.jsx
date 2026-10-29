import { Modal, Pressable, ScrollView, View } from 'react-native';
import Animated, { FadeIn, SlideInDown } from 'react-native-reanimated';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useTheme } from '../theme/ThemeProvider';
import { useShouldReduceMotion } from '../../hooks/useMotion';
import { AppText } from './AppText';
import { Button } from './Button';

export function BottomSheet({ visible, onClose, title, children, testID }) {
  const theme = useTheme();
  const insets = useSafeAreaInsets();
  const reduce = useShouldReduceMotion();
  return (
    <Modal visible={visible} transparent animationType="none" onRequestClose={onClose} statusBarTranslucent>
      <Animated.View entering={reduce ? undefined : FadeIn.duration(150)} style={{ flex: 1, backgroundColor: theme.colors.overlay, justifyContent: 'flex-end' }}>
        <Pressable accessibilityLabel="Close" style={{ flex: 1 }} onPress={onClose} />
        <Animated.View
          testID={testID}
          entering={reduce ? undefined : SlideInDown.duration(220)}
          accessibilityViewIsModal
          style={{
            backgroundColor: theme.colors.surface,
            borderTopLeftRadius: theme.radii.xl,
            borderTopRightRadius: theme.radii.xl,
            padding: theme.spacing.lg,
            paddingBottom: theme.spacing.lg + insets.bottom,
            gap: theme.spacing.md,
            maxHeight: '85%',
            width: '100%',
            maxWidth: 640,
            alignSelf: 'center',
            ...theme.elevation.sheet,
          }}
        >
          <View style={{ alignSelf: 'center', width: 40, height: 4, borderRadius: 2, backgroundColor: theme.colors.border }} />
          {title ? <AppText variant="heading" accessibilityRole="header">{title}</AppText> : null}
          <ScrollView style={{ flexShrink: 1 }} contentContainerStyle={{ gap: theme.spacing.md }} keyboardShouldPersistTaps="handled" showsVerticalScrollIndicator={false}>
            {children}
          </ScrollView>
        </Animated.View>
      </Animated.View>
    </Modal>
  );
}

/** Confirmation dialog for destructive or consequential actions. */
export function ConfirmDialog({ visible, title, message, confirmLabel = 'Confirm', destructive = false, loading, onConfirm, onCancel }) {
  const theme = useTheme();
  return (
    <Modal visible={visible} transparent animationType="fade" onRequestClose={onCancel}>
      <View style={{ flex: 1, backgroundColor: theme.colors.overlay, justifyContent: 'center', padding: 24 }}>
        <View
          accessibilityViewIsModal
          accessibilityRole="alert"
          style={{ backgroundColor: theme.colors.surface, borderRadius: theme.radii.lg, padding: 20, gap: 12, width: '100%', maxWidth: 420, alignSelf: 'center', borderWidth: 1, borderColor: theme.colors.border }}
        >
          <AppText variant="heading">{title}</AppText>
          {message ? <AppText tone="muted">{message}</AppText> : null}
          <View style={{ flexDirection: 'row', justifyContent: 'flex-end', gap: 8, marginTop: 4 }}>
            <Button title="Cancel" variant="secondary" size="sm" onPress={onCancel} />
            <Button title={confirmLabel} variant={destructive ? 'danger' : 'primary'} size="sm" loading={loading} onPress={onConfirm} />
          </View>
        </View>
      </View>
    </Modal>
  );
}
