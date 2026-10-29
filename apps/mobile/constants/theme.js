// QuantAI F&O design tokens. Dark is the primary experience; light is supported.

const palette = {
  navy950: '#05070F',
  navy900: '#0A0E1A',
  navy850: '#0E1322',
  charcoal800: '#141A2A',
  charcoal700: '#1B2235',
  charcoal600: '#252E45',
  slate500: '#5B6680',
  slate400: '#8A94AD',
  slate300: '#B4BCCF',
  slate100: '#E8ECF5',
  white: '#FFFFFF',
  purple500: '#8B5CF6',
  purple400: '#A78BFA',
  purple700: '#6D28D9',
  cyan400: '#22D3EE',
  blue500: '#3B82F6',
  emerald500: '#10B981',
  emerald400: '#34D399',
  red500: '#EF4444',
  red400: '#F87171',
  amber500: '#F59E0B',
  amber400: '#FBBF24',
};

const dark = {
  mode: 'dark',
  colors: {
    background: palette.navy900,
    backgroundDeep: palette.navy950,
    surface: palette.charcoal800,
    surfaceRaised: palette.charcoal700,
    border: palette.charcoal600,
    borderSubtle: '#1E2638',
    text: palette.slate100,
    textMuted: palette.slate400,
    textFaint: palette.slate500,
    textInverse: palette.navy950,
    primary: palette.purple500,
    primaryStrong: palette.purple700,
    primarySoft: 'rgba(139, 92, 246, 0.16)',
    secondary: palette.cyan400,
    info: palette.blue500,
    positive: palette.emerald400,
    positiveSoft: 'rgba(16, 185, 129, 0.14)',
    negative: palette.red400,
    negativeSoft: 'rgba(239, 68, 68, 0.14)',
    warning: palette.amber400,
    warningSoft: 'rgba(245, 158, 11, 0.14)',
    overlay: 'rgba(3, 5, 12, 0.72)',
    glow: 'rgba(139, 92, 246, 0.35)',
  },
};

const light = {
  mode: 'light',
  colors: {
    background: '#F5F6FA',
    backgroundDeep: '#ECEEF5',
    surface: palette.white,
    surfaceRaised: '#F9FAFD',
    border: '#DCE0EA',
    borderSubtle: '#E9ECF3',
    text: '#111827',
    textMuted: '#4B5563',
    textFaint: '#6B7280',
    textInverse: palette.white,
    primary: palette.purple700,
    primaryStrong: '#5B21B6',
    primarySoft: 'rgba(109, 40, 217, 0.10)',
    secondary: '#0891B2',
    info: '#2563EB',
    positive: '#047857',
    positiveSoft: 'rgba(4, 120, 87, 0.10)',
    negative: '#B91C1C',
    negativeSoft: 'rgba(185, 28, 28, 0.10)',
    warning: '#B45309',
    warningSoft: 'rgba(180, 83, 9, 0.10)',
    overlay: 'rgba(17, 24, 39, 0.45)',
    glow: 'rgba(109, 40, 217, 0.18)',
  },
};

export const spacing = { xxs: 2, xs: 4, sm: 8, md: 12, lg: 16, xl: 20, xxl: 24, xxxl: 32 };

export const radii = { sm: 6, md: 10, lg: 14, xl: 20, pill: 999 };

export const typography = {
  // Sizes stay >= 12 for accessibility; numeric figures use tabular digits.
  display: { fontSize: 28, lineHeight: 34, fontWeight: '700' },
  title: { fontSize: 22, lineHeight: 28, fontWeight: '700' },
  heading: { fontSize: 17, lineHeight: 23, fontWeight: '600' },
  body: { fontSize: 15, lineHeight: 21, fontWeight: '400' },
  bodyStrong: { fontSize: 15, lineHeight: 21, fontWeight: '600' },
  caption: { fontSize: 13, lineHeight: 18, fontWeight: '400' },
  label: { fontSize: 12, lineHeight: 16, fontWeight: '600', letterSpacing: 0.4 },
  mono: { fontSize: 15, lineHeight: 21, fontWeight: '600', fontVariant: ['tabular-nums'] },
};

export const elevation = {
  card: {
    shadowColor: '#000',
    shadowOpacity: 0.25,
    shadowRadius: 10,
    shadowOffset: { width: 0, height: 4 },
    elevation: 3,
  },
  sheet: {
    shadowColor: '#000',
    shadowOpacity: 0.4,
    shadowRadius: 20,
    shadowOffset: { width: 0, height: -6 },
    elevation: 12,
  },
};

export const chart = {
  candleUp: palette.emerald400,
  candleDown: palette.red400,
  grid: 'rgba(138, 148, 173, 0.12)',
  axis: palette.slate400,
  series: [palette.purple400, palette.cyan400, palette.amber400, palette.blue500, palette.emerald400],
};

export const breakpoints = { tablet: 768, desktop: 1100 };

export const themes = { dark, light };

export function buildTheme(mode = 'dark') {
  const base = themes[mode] ?? dark;
  return { ...base, spacing, radii, typography, elevation, chart, breakpoints };
}
