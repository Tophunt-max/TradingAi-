import Svg, { Defs, LinearGradient, Path, Rect, Stop } from 'react-native-svg';

// Original QuantAI mark: rounded tile with a rising candle-line motif.
export function BrandMark({ size = 48 }) {
  return (
    <Svg width={size} height={size} viewBox="0 0 64 64" accessibilityLabel="QuantAI logo">
      <Defs>
        <LinearGradient id="qa" x1="0" y1="0" x2="1" y2="1">
          <Stop offset="0" stopColor="#8B5CF6" />
          <Stop offset="1" stopColor="#22D3EE" />
        </LinearGradient>
      </Defs>
      <Rect x="2" y="2" width="60" height="60" rx="16" fill="#0E1322" stroke="url(#qa)" strokeWidth="2.5" />
      <Path d="M14 42 L24 32 L32 37 L44 22 L50 26" stroke="url(#qa)" strokeWidth="4" fill="none" strokeLinecap="round" strokeLinejoin="round" />
      <Rect x="18" y="44" width="4" height="8" rx="1" fill="#34D399" />
      <Rect x="30" y="41" width="4" height="11" rx="1" fill="#F87171" />
      <Rect x="42" y="38" width="4" height="14" rx="1" fill="#34D399" />
    </Svg>
  );
}
