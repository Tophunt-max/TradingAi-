import { useState } from 'react';
import { View } from 'react-native';
import Svg, { G, Line, Path, Rect, Text as SvgText } from 'react-native-svg';
import { useTheme } from '../theme/ThemeProvider';

const AXIS_W = 58;

function linePath(values, x, y) {
  let d = '';
  values.forEach((v, i) => {
    if (v == null) return;
    d += `${d ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
  });
  return d;
}

/**
 * Candlestick chart with optional overlay lines.
 * overlays: [{ key, values, color }]
 */
export function CandleChart({ candles, overlays = [], markers = [], zones = [], height = 260, testID }) {
  const theme = useTheme();
  const [width, setWidth] = useState(0);
  const c = theme.chart;
  const plotW = Math.max(width - AXIS_W, 10);

  const lows = candles.map((k) => k.low);
  const highs = candles.map((k) => k.high);
  overlays.forEach((o) => o.values.forEach((v) => v != null && (lows.push(v), highs.push(v))));
  const min = Math.min(...lows);
  const max = Math.max(...highs);
  const pad = (max - min) * 0.06 || 1;
  const lo = min - pad;
  const hi = max + pad;
  const y = (v) => ((hi - v) / (hi - lo)) * height;
  const step = plotW / Math.max(candles.length, 1);
  const x = (i) => i * step + step / 2;
  const bodyW = Math.max(step * 0.62, 1);
  const last = candles[candles.length - 1];
  const ticks = Array.from({ length: 5 }, (_, i) => lo + ((hi - lo) * (i + 0.5)) / 5);

  return (
    <View
      testID={testID}
      accessibilityRole="image"
      accessibilityLabel={last ? `Candlestick chart, last close ${last.close}` : 'Candlestick chart'}
      onLayout={(e) => setWidth(e.nativeEvent.layout.width)}
      style={{ height }}
    >
      {width > 0 && candles.length > 0 ? (
        <Svg width={width} height={height}>
          {ticks.map((t) => (
            <G key={t}>
              <Line x1={0} x2={plotW} y1={y(t)} y2={y(t)} stroke={c.grid} strokeWidth={1} />
              <SvgText x={plotW + 6} y={y(t) + 4} fill={c.axis} fontSize={10}>
                {t.toFixed(t > 1000 ? 0 : 2)}
              </SvgText>
            </G>
          ))}
          {zones.map((z, i) => (
            <Rect
              key={`z${i}`}
              x={x(z.start) - step / 2}
              y={y(z.top)}
              width={Math.max((z.end - z.start + 1) * step, step)}
              height={Math.max(y(z.bottom) - y(z.top), 1)}
              fill={z.direction === 'BULLISH' ? c.candleUp : c.candleDown}
              opacity={0.13}
            />
          ))}
          {candles.map((k, i) => {
            const up = k.close >= k.open;
            const color = up ? c.candleUp : c.candleDown;
            const top = y(Math.max(k.open, k.close));
            const h = Math.max(Math.abs(y(k.open) - y(k.close)), 1);
            return (
              <G key={k.ts}>
                <Line x1={x(i)} x2={x(i)} y1={y(k.high)} y2={y(k.low)} stroke={color} strokeWidth={1} />
                <Rect x={x(i) - bodyW / 2} y={top} width={bodyW} height={h} fill={color} />
              </G>
            );
          })}
          {overlays.map((o) => (
            <Path key={o.key} d={linePath(o.values, x, y)} stroke={o.color} strokeWidth={1.5} fill="none" />
          ))}
          {markers.map((m, i) => {
            const k = candles[m.index];
            if (!k) return null;
            const bull = m.direction === 'BULLISH';
            const px = x(m.index);
            const py = bull ? Math.min(y(k.low) + 10, height - 2) : Math.max(y(k.high) - 10, 2);
            const d = bull ? `M${px},${py - 5} L${px - 4},${py + 2} L${px + 4},${py + 2} Z` : `M${px},${py + 5} L${px - 4},${py - 2} L${px + 4},${py - 2} Z`;
            return <Path key={`m${i}`} d={d} fill={bull ? c.candleUp : c.candleDown} />;
          })}
          {last ? (
            <G>
              <Line x1={0} x2={plotW} y1={y(last.close)} y2={y(last.close)} stroke={theme.colors.primary} strokeDasharray="4,4" strokeWidth={1} />
              <Rect x={plotW + 2} y={y(last.close) - 9} width={AXIS_W - 4} height={18} rx={4} fill={last.close >= last.open ? c.candleUp : c.candleDown} />
              <SvgText x={plotW + 6} y={y(last.close) + 4} fill="#05070F" fontSize={10} fontWeight="700">
                {last.close.toFixed(last.close > 1000 ? 1 : 2)}
              </SvgText>
            </G>
          ) : null}
        </Svg>
      ) : null}
    </View>
  );
}

/** Indicator sub-panel. lines: [{ key, values, color }], bars: values for histogram. */
export function IndicatorPanel({ lines = [], bars, height = 90, range, guides = [], testID }) {
  const theme = useTheme();
  const [width, setWidth] = useState(0);
  const plotW = Math.max(width - AXIS_W, 10);
  const all = [...lines.flatMap((l) => l.values), ...(bars ?? [])].filter((v) => v != null);
  const lo = range ? range[0] : Math.min(...all, 0);
  const hi = range ? range[1] : Math.max(...all, 0);
  const span = hi - lo || 1;
  const y = (v) => ((hi - v) / span) * height;
  const n = lines[0]?.values.length ?? bars?.length ?? 0;
  const step = plotW / Math.max(n, 1);
  const x = (i) => i * step + step / 2;

  return (
    <View testID={testID} onLayout={(e) => setWidth(e.nativeEvent.layout.width)} style={{ height }}>
      {width > 0 && n > 0 ? (
        <Svg width={width} height={height}>
          {guides.map((g) => (
            <G key={g}>
              <Line x1={0} x2={plotW} y1={y(g)} y2={y(g)} stroke={theme.chart.grid} strokeDasharray="3,3" />
              <SvgText x={plotW + 6} y={y(g) + 4} fill={theme.chart.axis} fontSize={10}>{g}</SvgText>
            </G>
          ))}
          {bars?.map((v, i) =>
            v == null ? null : (
              <Rect
                key={i}
                x={x(i) - step * 0.3}
                y={Math.min(y(v), y(0))}
                width={Math.max(step * 0.6, 1)}
                height={Math.max(Math.abs(y(v) - y(0)), 0.5)}
                fill={v >= 0 ? theme.chart.candleUp : theme.chart.candleDown}
                opacity={0.7}
              />
            ),
          )}
          {lines.map((l) => (
            <Path key={l.key} d={linePath(l.values, x, y)} stroke={l.color} strokeWidth={1.5} fill="none" />
          ))}
        </Svg>
      ) : null}
    </View>
  );
}
