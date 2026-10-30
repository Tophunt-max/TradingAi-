import { View } from 'react-native';
import { AccountModeBadge, AppText } from '../../components/ui';
import { useAccountMode } from './mode';

/** The ACTIVE account mode (from the server), for screens that show account-scoped money, orders or positions. */
export function ActiveModeBadge({ testID }) {
  const { mode } = useAccountMode();
  return <AccountModeBadge mode={mode} testID={testID} />;
}

/** "Demo account · virtual funds" or "Real account · broker funds". */
export function accountSubtitle(mode, extra) {
  const base = mode === 'REAL' ? 'Real account · broker funds' : 'Demo account · virtual funds';
  return extra ? `${base} · ${extra}` : base;
}

/** One-line strip for account-scoped screens: the active mode badge plus a short note. */
export function ActiveModeHeader({ note, testID = 'active-mode-header' }) {
  const { mode } = useAccountMode();
  return (
    <View testID={testID} style={{ flexDirection: 'row', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
      <AccountModeBadge mode={mode} />
      <AppText variant="caption" tone="faint">{note ?? (mode === 'REAL' ? 'Showing your real account.' : 'Showing your demo account (virtual money).')}</AppText>
    </View>
  );
}
