import { View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { useTheme } from '../theme/ThemeProvider';
import { AppText } from './AppText';
import { Button } from './Button';
import { SkeletonList } from './Skeleton';

const PRESETS = {
  empty: { icon: 'file-tray-outline', tone: 'textMuted', title: 'Nothing here yet' },
  error: { icon: 'alert-circle-outline', tone: 'negative', title: 'Something went wrong' },
  offline: { icon: 'cloud-offline-outline', tone: 'warning', title: 'You are offline' },
  stale: { icon: 'time-outline', tone: 'warning', title: 'Data may be outdated' },
  denied: { icon: 'lock-closed-outline', tone: 'warning', title: 'Permission required' },
  unavailable: { icon: 'construct-outline', tone: 'info', title: 'Not available yet' },
};

/** kind: empty | error | offline | stale | denied | unavailable | loading */
export function StateView({ kind = 'empty', title, message, actionLabel, onAction, compact = false, testID }) {
  const theme = useTheme();
  if (kind === 'loading') return <SkeletonList rows={compact ? 2 : 4} />;
  const p = PRESETS[kind] ?? PRESETS.empty;
  return (
    <View
      testID={testID ?? `state-${kind}`}
      accessibilityRole={kind === 'error' ? 'alert' : undefined}
      style={{ alignItems: 'center', paddingVertical: compact ? 12 : 28, gap: 8 }}
    >
      <Ionicons name={p.icon} size={compact ? 22 : 34} color={theme.colors[p.tone]} />
      <AppText variant="bodyStrong" style={{ textAlign: 'center' }}>{title ?? p.title}</AppText>
      {message ? <AppText variant="caption" tone="muted" style={{ textAlign: 'center', maxWidth: 420 }}>{message}</AppText> : null}
      {actionLabel && onAction ? <Button title={actionLabel} size="sm" variant="secondary" onPress={onAction} /> : null}
    </View>
  );
}

/** Maps an ApiError / network failure onto the right state preset. */
export function QueryState({ query, emptyMessage, children, isEmpty }) {
  if (query.isLoading) return <StateView kind="loading" />;
  if (query.isError) {
    const err = query.error;
    const kind = err?.isNetwork ? 'offline' : err?.status === 403 ? 'denied' : err?.status === 503 ? 'unavailable' : 'error';
    return <StateView kind={kind} message={err?.message} actionLabel="Retry" onAction={() => query.refetch()} />;
  }
  if (isEmpty?.(query.data)) return <StateView kind="empty" message={emptyMessage} />;
  return children(query.data);
}
