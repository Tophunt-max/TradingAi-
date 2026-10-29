import { useState } from 'react';
import { Pressable, ScrollView, Switch, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { useTheme } from '../theme/ThemeProvider';
import { useHaptics } from '../../hooks/useHaptics';
import { AppText } from './AppText';
import { BottomSheet } from './BottomSheet';

/** Dropdown that opens a bottom sheet of options. options: [{ value, label }] */
export function Select({ label, value, options, onChange, testID }) {
  const theme = useTheme();
  const [open, setOpen] = useState(false);
  const current = options.find((o) => o.value === value);
  return (
    <View style={{ gap: 6 }}>
      {label ? <AppText variant="label" tone="muted">{label.toUpperCase()}</AppText> : null}
      <Pressable
        testID={testID}
        accessibilityRole="button"
        accessibilityLabel={`${label}: ${current?.label ?? 'Not set'}`}
        onPress={() => setOpen(true)}
        style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', borderWidth: 1, borderColor: theme.colors.border, borderRadius: theme.radii.md, paddingHorizontal: 12, minHeight: 48, backgroundColor: theme.colors.surface }}
      >
        <AppText>{current?.label ?? 'Select…'}</AppText>
        <Ionicons name="chevron-down" size={18} color={theme.colors.textMuted} />
      </Pressable>
      <BottomSheet visible={open} onClose={() => setOpen(false)} title={label}>
        <ScrollView>
          {options.map((o) => (
            <ListRow
              key={o.value}
              title={o.label}
              right={o.value === value ? <Ionicons name="checkmark" size={18} color={theme.colors.primary} /> : null}
              onPress={() => {
                onChange(o.value);
                setOpen(false);
              }}
            />
          ))}
        </ScrollView>
      </BottomSheet>
    </View>
  );
}

export function SegmentedControl({ options, value, onChange, testID }) {
  const theme = useTheme();
  const { tap } = useHaptics();
  return (
    <View testID={testID} accessibilityRole="tablist" style={{ flexDirection: 'row', backgroundColor: theme.colors.surfaceRaised, borderRadius: theme.radii.md, padding: 3 }}>
      {options.map((o) => {
        const active = o.value === value;
        return (
          <Pressable
            key={o.value}
            accessibilityRole="tab"
            accessibilityState={{ selected: active }}
            onPress={() => {
              tap();
              onChange(o.value);
            }}
            style={{ flex: 1, paddingVertical: 8, borderRadius: theme.radii.sm, alignItems: 'center', backgroundColor: active ? theme.colors.primary : 'transparent' }}
          >
            <AppText variant="caption" style={{ color: active ? '#FFFFFF' : theme.colors.textMuted, fontWeight: '600' }}>{o.label}</AppText>
          </Pressable>
        );
      })}
    </View>
  );
}

export function ListRow({ title, subtitle, icon, right, onPress, destructive = false, testID, chevron }) {
  const theme = useTheme();
  const content = (
    <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12, paddingVertical: 12, minHeight: 48 }}>
      {icon ? <Ionicons name={icon} size={20} color={destructive ? theme.colors.negative : theme.colors.textMuted} /> : null}
      <View style={{ flex: 1 }}>
        <AppText tone={destructive ? 'negative' : 'default'}>{title}</AppText>
        {subtitle ? <AppText variant="caption" tone="muted">{subtitle}</AppText> : null}
      </View>
      {right}
      {(chevron ?? !!onPress) && !right ? <Ionicons name="chevron-forward" size={18} color={theme.colors.textFaint} /> : null}
    </View>
  );
  if (!onPress) return <View testID={testID}>{content}</View>;
  return (
    <Pressable testID={testID} accessibilityRole="button" accessibilityLabel={title} onPress={onPress} style={({ pressed }) => ({ opacity: pressed ? 0.7 : 1 })}>
      {content}
    </Pressable>
  );
}

export function ToggleRow({ title, subtitle, value, onValueChange, disabled }) {
  const theme = useTheme();
  return (
    <ListRow
      title={title}
      subtitle={subtitle}
      right={
        <Switch
          accessibilityLabel={title}
          value={value}
          disabled={disabled}
          onValueChange={onValueChange}
          trackColor={{ true: theme.colors.primary, false: theme.colors.border }}
          thumbColor="#FFFFFF"
        />
      }
    />
  );
}

export function Divider() {
  const theme = useTheme();
  return <View style={{ height: 1, backgroundColor: theme.colors.borderSubtle }} />;
}

/** Tap-to-reveal explanatory text (works on touch and web). */
export function Tooltip({ text, children }) {
  const theme = useTheme();
  const [open, setOpen] = useState(false);
  return (
    <View>
      <Pressable accessibilityRole="button" accessibilityHint="Shows an explanation" onPress={() => setOpen((o) => !o)} style={{ flexDirection: 'row', alignItems: 'center', gap: 4 }}>
        {children}
        <Ionicons name="information-circle-outline" size={15} color={theme.colors.textMuted} />
      </Pressable>
      {open ? (
        <View accessibilityLiveRegion="polite" style={{ marginTop: 6, padding: 10, borderRadius: theme.radii.sm, backgroundColor: theme.colors.surfaceRaised, borderWidth: 1, borderColor: theme.colors.border }}>
          <AppText variant="caption" tone="muted">{text}</AppText>
        </View>
      ) : null}
    </View>
  );
}

/**
 * Compact financial table. columns: [{ key, title, align, render, flex }]
 */
export function DataTable({ columns, rows, keyExtractor, testID }) {
  const theme = useTheme();
  return (
    <View testID={testID} accessibilityRole="list">
      <View style={{ flexDirection: 'row', paddingVertical: 8, borderBottomWidth: 1, borderColor: theme.colors.borderSubtle }}>
        {columns.map((col) => (
          <AppText key={col.key} variant="label" tone="faint" style={{ flex: col.flex ?? 1, textAlign: col.align ?? 'left' }}>
            {col.title.toUpperCase()}
          </AppText>
        ))}
      </View>
      {rows.map((row, i) => (
        <View key={keyExtractor ? keyExtractor(row) : i} accessibilityRole="text" style={{ flexDirection: 'row', alignItems: 'center', paddingVertical: 10, borderBottomWidth: i === rows.length - 1 ? 0 : 1, borderColor: theme.colors.borderSubtle }}>
          {columns.map((col) => (
            <View key={col.key} style={{ flex: col.flex ?? 1, alignItems: col.align === 'right' ? 'flex-end' : 'flex-start' }}>
              {col.render ? col.render(row) : <AppText variant="mono">{String(row[col.key] ?? '—')}</AppText>}
            </View>
          ))}
        </View>
      ))}
    </View>
  );
}
