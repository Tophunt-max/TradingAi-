import { useState } from 'react';
import { Pressable, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { useTheme } from '../theme/ThemeProvider';
import { AppText } from './AppText';
import { Badge } from './Badge';
import { BottomSheet } from './BottomSheet';

/**
 * Account modes. DEMO = virtual ledger (simulated money). REAL = funds, orders and
 * positions from a connected broker. The two are never mixed in one total.
 * Note: "SIMULATED DATA" (synthetic market prices) is a separate concept, shown by
 * DataSourceBadge, and can apply to either mode's screens.
 */
export const ACCOUNT_MODES = {
  DEMO: { label: 'DEMO', badge: 'DEMO · VIRTUAL', tone: 'primary', icon: 'flask-outline', title: 'Demo account', subtitle: 'Virtual money, simulated orders and fills' },
  REAL: { label: 'REAL', badge: 'REAL', tone: 'negative', icon: 'business-outline', title: 'Real account', subtitle: 'Funds, orders and positions from your broker' },
};

export function AccountModeBadge({ mode = 'DEMO', testID }) {
  const m = ACCOUNT_MODES[mode] ?? ACCOUNT_MODES.DEMO;
  return <Badge label={m.badge} tone={m.tone} testID={testID ?? `account-mode-${mode.toLowerCase()}`} />;
}

/**
 * Compact pill showing the active account mode; opens a sheet to switch.
 * options: [{ mode, available, reason }]. Unavailable modes are shown with the
 * reason and cannot be selected.
 */
export function AccountModeSelector({ value, options, onChange, testID = 'account-mode-selector' }) {
  const theme = useTheme();
  const [open, setOpen] = useState(false);
  const cur = ACCOUNT_MODES[value] ?? ACCOUNT_MODES.DEMO;
  const fg = theme.colors[cur.tone === 'negative' ? 'negative' : 'primary'];
  return (
    <>
      <Pressable
        testID={testID}
        accessibilityRole="button"
        accessibilityLabel={`Account mode: ${cur.title}. Change account mode`}
        onPress={() => setOpen(true)}
        hitSlop={6}
        style={{ flexDirection: 'row', alignItems: 'center', gap: 6, alignSelf: 'flex-start', paddingHorizontal: 12, paddingVertical: 8, minHeight: 40, borderRadius: theme.radii.pill, borderWidth: 1, borderColor: fg, backgroundColor: theme.colors.surface }}
      >
        <Ionicons name={cur.icon} size={16} color={fg} />
        <AppText variant="bodyStrong" style={{ color: fg }}>{cur.title}</AppText>
        <Ionicons name="chevron-down" size={14} color={theme.colors.textMuted} />
      </Pressable>
      <BottomSheet visible={open} onClose={() => setOpen(false)} title="Account mode" testID="account-mode-sheet">
        {options.map((o) => {
          const m = ACCOUNT_MODES[o.mode];
          const selected = o.mode === value;
          return (
            <Pressable
              key={o.mode}
              testID={`account-mode-option-${o.mode.toLowerCase()}`}
              accessibilityRole="radio"
              accessibilityState={{ selected, disabled: !o.available }}
              disabled={!o.available}
              onPress={() => {
                if (!selected) onChange?.(o.mode);
                setOpen(false);
              }}
              style={{ flexDirection: 'row', gap: 12, padding: 14, borderRadius: theme.radii.md, borderWidth: 1, borderColor: selected ? theme.colors.primary : theme.colors.border, backgroundColor: selected ? theme.colors.primarySoft : theme.colors.surfaceRaised, opacity: o.available ? 1 : 0.6 }}
            >
              <Ionicons name={m.icon} size={22} color={theme.colors[m.tone === 'negative' ? 'negative' : 'primary']} />
              <View style={{ flex: 1, gap: 2 }}>
                <View style={{ flexDirection: 'row', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
                  <AppText variant="bodyStrong">{m.title}</AppText>
                  {selected ? <Badge label="ACTIVE" tone="positive" /> : null}
                  {!o.available ? <Badge label="UNAVAILABLE" tone="warning" /> : null}
                </View>
                <AppText variant="caption" tone="muted">{m.subtitle}</AppText>
                {o.reason ? <AppText variant="caption" tone={o.available ? 'faint' : 'warning'}>{o.reason}</AppText> : null}
              </View>
              {selected ? <Ionicons name="checkmark-circle" size={20} color={theme.colors.primary} /> : null}
            </Pressable>
          );
        })}
        <AppText variant="caption" tone="faint">
          Demo and real balances, orders, positions and P&L are kept separate. Switching mode never moves positions between accounts.
        </AppText>
      </BottomSheet>
    </>
  );
}
