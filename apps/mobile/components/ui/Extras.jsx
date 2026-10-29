import { Pressable, ScrollView, View } from 'react-native';
import Svg, { Circle } from 'react-native-svg';
import { Ionicons } from '@expo/vector-icons';
import { useTheme } from '../theme/ThemeProvider';
import { useHaptics } from '../../hooks/useHaptics';
import { AppText } from './AppText';

export function Chip({ label, selected, onPress, testID, icon }) {
  const theme = useTheme();
  const { tap } = useHaptics();
  return (
    <Pressable
      testID={testID}
      accessibilityRole="button"
      accessibilityState={{ selected: !!selected }}
      accessibilityLabel={label}
      onPress={() => {
        tap();
        onPress?.();
      }}
      style={{
        flexDirection: 'row',
        alignItems: 'center',
        gap: 4,
        paddingHorizontal: 12,
        paddingVertical: 7,
        borderRadius: theme.radii.pill,
        borderWidth: 1,
        borderColor: selected ? theme.colors.primary : theme.colors.border,
        backgroundColor: selected ? theme.colors.primarySoft : theme.colors.surface,
      }}
    >
      {icon ? <Ionicons name={icon} size={14} color={selected ? theme.colors.primary : theme.colors.textMuted} /> : null}
      <AppText variant="caption" style={{ fontWeight: '600', color: selected ? theme.colors.primary : theme.colors.textMuted }}>
        {label}
      </AppText>
    </Pressable>
  );
}

export function ChipRow({ options, value, onChange, testIDPrefix }) {
  return (
    <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 8 }}>
      {options.map((o) => (
        <Chip key={o.value} label={o.label} selected={o.value === value} onPress={() => onChange(o.value)} testID={testIDPrefix ? `${testIDPrefix}-${o.value}` : undefined} />
      ))}
    </ScrollView>
  );
}

export function StatTile({ label, value, sub, tone = 'default', style }) {
  const theme = useTheme();
  return (
    <View style={[{ flex: 1, minWidth: 120, padding: 12, borderRadius: theme.radii.md, backgroundColor: theme.colors.surfaceRaised, gap: 2 }, style]}>
      <AppText variant="caption" tone="muted">{label}</AppText>
      <AppText variant="heading" tone={tone} style={{ fontVariant: ['tabular-nums'] }}>{value}</AppText>
      {sub ? <AppText variant="caption" tone="faint">{sub}</AppText> : null}
    </View>
  );
}

export function QuickAction({ icon, label, onPress, testID }) {
  const theme = useTheme();
  return (
    <Pressable testID={testID} accessibilityRole="button" accessibilityLabel={label} onPress={onPress} style={({ pressed }) => ({ flex: 1, alignItems: 'center', gap: 6, opacity: pressed ? 0.7 : 1 })}>
      <View style={{ width: 52, height: 52, borderRadius: 16, backgroundColor: theme.colors.primarySoft, alignItems: 'center', justifyContent: 'center', borderWidth: 1, borderColor: theme.colors.border }}>
        <Ionicons name={icon} size={22} color={theme.colors.primary} />
      </View>
      <AppText variant="caption" tone="muted" numberOfLines={1}>{label}</AppText>
    </Pressable>
  );
}

export function Stepper({ steps, current }) {
  const theme = useTheme();
  return (
    <View accessibilityLabel={`Step ${current + 1} of ${steps.length}: ${steps[current]}`} style={{ flexDirection: 'row', alignItems: 'flex-start' }}>
      {steps.map((s, i) => {
        const done = i < current;
        const active = i === current;
        return (
          <View key={s} style={{ flex: 1, alignItems: 'center', gap: 4 }}>
            <View style={{ flexDirection: 'row', alignItems: 'center', width: '100%' }}>
              <View style={{ flex: 1, height: 2, backgroundColor: i === 0 ? 'transparent' : i <= current ? theme.colors.primary : theme.colors.border }} />
              <View style={{ width: 28, height: 28, borderRadius: 14, alignItems: 'center', justifyContent: 'center', backgroundColor: done || active ? theme.colors.primary : theme.colors.surfaceRaised, borderWidth: 1, borderColor: active ? theme.colors.primary : theme.colors.border }}>
                {done ? <Ionicons name="checkmark" size={14} color="#fff" /> : <AppText variant="caption" style={{ color: active ? '#fff' : theme.colors.textMuted, fontWeight: '700' }}>{i + 1}</AppText>}
              </View>
              <View style={{ flex: 1, height: 2, backgroundColor: i === steps.length - 1 ? 'transparent' : i < current ? theme.colors.primary : theme.colors.border }} />
            </View>
            <AppText variant="caption" tone={active ? 'default' : 'faint'} numberOfLines={1}>{s}</AppText>
          </View>
        );
      })}
    </View>
  );
}

/** Circular usage gauge (0–100%). */
export function ProgressRing({ percent, size = 120, label, sublabel }) {
  const theme = useTheme();
  const stroke = 10;
  const r = (size - stroke) / 2;
  const circ = 2 * Math.PI * r;
  const p = Math.min(Math.max(percent, 0), 100);
  const color = p >= 80 ? theme.colors.negative : p >= 50 ? theme.colors.warning : theme.colors.positive;
  return (
    <View accessibilityRole="progressbar" accessibilityValue={{ min: 0, max: 100, now: Math.round(p) }} style={{ width: size, height: size, alignItems: 'center', justifyContent: 'center' }}>
      <Svg width={size} height={size} style={{ position: 'absolute' }}>
        <Circle cx={size / 2} cy={size / 2} r={r} stroke={theme.colors.surfaceRaised} strokeWidth={stroke} fill="none" />
        <Circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          stroke={color}
          strokeWidth={stroke}
          fill="none"
          strokeDasharray={`${circ}`}
          strokeDashoffset={circ * (1 - p / 100)}
          strokeLinecap="round"
          transform={`rotate(-90 ${size / 2} ${size / 2})`}
        />
      </Svg>
      <AppText variant="title">{label ?? `${Math.round(p)}%`}</AppText>
      {sublabel ? <AppText variant="caption" tone="muted">{sublabel}</AppText> : null}
    </View>
  );
}

export function NoticeBanner({ tone = 'warning', icon = 'information-circle-outline', children, testID }) {
  const theme = useTheme();
  const map = {
    warning: [theme.colors.warningSoft, theme.colors.warning],
    info: ['rgba(59,130,246,0.12)', theme.colors.info],
    danger: [theme.colors.negativeSoft, theme.colors.negative],
    primary: [theme.colors.primarySoft, theme.colors.primary],
  };
  const [bg, fg] = map[tone] ?? map.warning;
  return (
    <View testID={testID} style={{ flexDirection: 'row', gap: 8, padding: 10, borderRadius: theme.radii.md, backgroundColor: bg, alignItems: 'flex-start' }}>
      <Ionicons name={icon} size={16} color={fg} style={{ marginTop: 2 }} />
      <AppText variant="caption" style={{ color: fg, flex: 1 }}>{children}</AppText>
    </View>
  );
}

export function KeyValue({ label, value, tone }) {
  return (
    <View style={{ flexDirection: 'row', justifyContent: 'space-between', paddingVertical: 6, gap: 12 }}>
      <AppText tone="muted" style={{ flexShrink: 1 }}>{label}</AppText>
      <AppText variant="bodyStrong" tone={tone} style={{ textAlign: 'right', fontVariant: ['tabular-nums'], flexShrink: 1 }}>{value}</AppText>
    </View>
  );
}

export function Fab({ icon = 'add', label, onPress, testID }) {
  const theme = useTheme();
  return (
    <Pressable
      testID={testID}
      accessibilityRole="button"
      accessibilityLabel={label}
      onPress={onPress}
      style={({ pressed }) => ({ flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8, backgroundColor: theme.colors.primary, borderRadius: theme.radii.md, paddingVertical: 14, opacity: pressed ? 0.85 : 1, shadowColor: theme.colors.glow, shadowOpacity: 1, shadowRadius: 14 })}
    >
      <Ionicons name={icon} size={18} color="#fff" />
      <AppText variant="bodyStrong" style={{ color: '#fff' }}>{label}</AppText>
    </Pressable>
  );
}
