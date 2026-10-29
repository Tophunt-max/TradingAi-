import { useState } from 'react';
import { Pressable, View } from 'react-native';
import { useLocalSearchParams } from 'expo-router';
import { AppText, BottomSheet, Card, ChipRow, DataSourceBadge, KeyValue, NoticeBanner, QueryState, Screen, SegmentedControl, StatTile } from '../../components/ui';
import { useTheme } from '../../components/theme/ThemeProvider';
import { useExpiries, useOptionChain } from '../../features/market/api';
import { isStale } from '../../features/system/api';
import { fixed, formatINR, formatIST, formatPrice } from '../../services/format';
import { QualityBanner } from '../../features/market/QualityBanner';

const fmtOI = (n) => (n == null ? '—' : Math.abs(n) >= 1e5 ? `${(n / 1e5).toFixed(1)}L` : n.toLocaleString('en-IN'));
const fmtStrike = (k) => (Number.isInteger(k) ? String(k) : fixed(k, 1));
const spreadPct = (s) => (s?.bid && s?.ask && s?.ltp ? ((s.ask - s.bid) / s.ltp) * 100 : null);
const fmtDate = (iso) => new Date(`${iso}T00:00:00Z`).toLocaleDateString('en-IN', { day: '2-digit', month: 'short', timeZone: 'UTC' });

function Cell({ children, flex = 1, align = 'center', strong, tone }) {
  return (
    <View style={{ flex, alignItems: align === 'center' ? 'center' : align === 'right' ? 'flex-end' : 'flex-start' }}>
      <AppText variant={strong ? 'bodyStrong' : 'caption'} tone={tone} style={{ fontVariant: ['tabular-nums'] }}>{children}</AppText>
    </View>
  );
}

function SideDetail({ title, side, lotSize }) {
  if (!side) return <View style={{ flex: 1, minWidth: 150 }}><AppText variant="bodyStrong">{title}</AppText><AppText tone="muted">No contract at this strike.</AppText></View>;
  const sp = spreadPct(side);
  return (
    <View style={{ flex: 1, minWidth: 150 }}>
      <AppText variant="bodyStrong">{title}</AppText>
      {side.trading_symbol ? <AppText variant="caption" tone="faint">{side.trading_symbol}</AppText> : null}
      <KeyValue label="LTP" value={fixed(side.ltp)} />
      <KeyValue label="Bid / Ask" value={`${fixed(side.bid)} / ${fixed(side.ask)}`} />
      <KeyValue label="Spread" value={sp == null ? '—' : `${sp.toFixed(2)}%`} tone={sp != null && sp > 2 ? 'warning' : undefined} />
      <KeyValue label="IV" value={side.iv == null ? '—' : `${fixed(side.iv)}%`} />
      <KeyValue label="Delta" value={fixed(side.delta, 3)} />
      <KeyValue label="Gamma" value={fixed(side.gamma, 5)} />
      <KeyValue label="Theta / day" value={fixed(side.theta)} />
      <KeyValue label="Vega" value={fixed(side.vega)} />
      <KeyValue label="OI (chg)" value={`${fmtOI(side.oi)} (${side.oi_change > 0 ? '+' : ''}${fmtOI(side.oi_change)})`} />
      <KeyValue label="Volume" value={fmtOI(side.volume)} />
      <KeyValue label="Premium for 1 lot" value={lotSize && side.ask ? formatINR(side.ask * lotSize) : '—'} />
    </View>
  );
}

export default function OptionChain() {
  const theme = useTheme();
  const params = useLocalSearchParams();
  const [underlying, setUnderlying] = useState(params.underlying === 'BANKNIFTY' ? 'BANKNIFTY' : 'NIFTY');
  const [expiry, setExpiry] = useState(null);
  const [view, setView] = useState('LTP');
  const [selected, setSelected] = useState(null);
  const expiries = useExpiries(underlying);
  const chain = useOptionChain(underlying, expiry, 8);

  return (
    <Screen edges={['bottom']}>
      <SegmentedControl
        value={underlying}
        onChange={(u) => {
          setUnderlying(u);
          setExpiry(null);
        }}
        options={[{ value: 'NIFTY', label: 'NIFTY' }, { value: 'BANKNIFTY', label: 'BANKNIFTY' }]} />
      {expiries.data ? (
        <ChipRow
          value={expiry ?? expiries.data.expiries[0]}
          onChange={setExpiry}
          options={expiries.data.expiries.map((e) => ({ value: e, label: fmtDate(e) }))}
          testIDPrefix="expiry"
        />
      ) : null}
      <QueryState query={chain}>
        {(d) => (
          <>
            <View style={{ flexDirection: 'row', gap: 10, flexWrap: 'wrap' }}>
              <StatTile label="Spot" value={formatPrice(d.spot)} sub={`ATM ${d.atm_strike == null ? '—' : fmtStrike(d.atm_strike)}`} />
              <StatTile label="PCR (OI)" value={d.pcr ?? '—'} sub={`Lot size: ${d.lot_size ?? 'unavailable'}${d.tick_size ? ` · tick ${d.tick_size}` : ''}`} />
            </View>
            {d.lot_size ? null : <NoticeBanner testID="chain-metadata-note">{d.metadata_note}</NoticeBanner>}
            <QualityBanner payload={d} testID="chain-quality" />
            <SegmentedControl value={view} onChange={setView} options={[{ value: 'LTP', label: 'LTP & OI' }, { value: 'GREEKS', label: 'IV & Delta' }]} />
            <Card padded={false} testID="option-chain-table">
              <View style={{ flexDirection: 'row', justifyContent: 'space-between', padding: 12, paddingBottom: 4 }}>
                <AppText variant="label" tone="positive">CALLS (CE)</AppText>
                <DataSourceBadge source={d.source} stale={isStale(d.as_of) || d.quality?.status === 'FAIL'} />
                <AppText variant="label" tone="negative">PUTS (PE)</AppText>
              </View>
              <View style={{ flexDirection: 'row', paddingHorizontal: 8, paddingVertical: 6, borderBottomWidth: 1, borderColor: theme.colors.borderSubtle }}>
                {(view === 'LTP' ? ['OI', 'LTP', 'STRIKE', 'LTP', 'OI'] : ['IV', 'Δ', 'STRIKE', 'Δ', 'IV']).map((h, i) => (
                  <Cell key={i} flex={i === 2 ? 1.2 : 1}><AppText variant="label" tone="faint">{h}</AppText></Cell>
                ))}
              </View>
              {d.rows.map((r) => {
                const callItm = d.spot != null && r.strike < d.spot;
                const c = r.call ?? {};
                const pt = r.put ?? {};
                return (
                  <Pressable
                    key={r.strike}
                    testID={`strike-${r.strike}`}
                    accessibilityRole="button"
                    accessibilityLabel={`Strike ${r.strike} details`}
                    onPress={() => setSelected(r)}
                    style={{ flexDirection: 'row', paddingHorizontal: 8, paddingVertical: 9, borderBottomWidth: 1, borderColor: theme.colors.borderSubtle, backgroundColor: r.is_atm ? theme.colors.primarySoft : 'transparent' }}
                  >
                    <View style={{ flex: 2, flexDirection: 'row', backgroundColor: callItm ? 'rgba(16,185,129,0.06)' : 'transparent', borderRadius: 4 }}>
                      {view === 'LTP' ? (
                        <>
                          <Cell>{fmtOI(c.oi)}</Cell>
                          <Cell strong>{fixed(c.ltp)}</Cell>
                        </>
                      ) : (
                        <>
                          <Cell>{fixed(c.iv, 1)}</Cell>
                          <Cell strong>{fixed(c.delta)}</Cell>
                        </>
                      )}
                    </View>
                    <Cell flex={1.2} strong tone={r.is_atm ? 'primary' : undefined}>{fmtStrike(r.strike)}</Cell>
                    <View style={{ flex: 2, flexDirection: 'row', backgroundColor: !callItm ? 'rgba(239,68,68,0.06)' : 'transparent', borderRadius: 4 }}>
                      {view === 'LTP' ? (
                        <>
                          <Cell strong>{fixed(pt.ltp)}</Cell>
                          <Cell>{fmtOI(pt.oi)}</Cell>
                        </>
                      ) : (
                        <>
                          <Cell strong>{fixed(pt.delta)}</Cell>
                          <Cell>{fixed(pt.iv, 1)}</Cell>
                        </>
                      )}
                    </View>
                  </Pressable>
                );
              })}
            </Card>
            <AppText variant="caption" tone="faint">
              {d.provider} · Greeks: {d.greeks_model}. As of {formatIST(d.as_of)} IST. Shaded cells are in the money.
            </AppText>
            <BottomSheet visible={!!selected} onClose={() => setSelected(null)} title={selected ? `${underlying} ${fmtStrike(selected.strike)} · ${fmtDate(d.expiry)}` : ''}>
              {selected ? (
                <View style={{ gap: 12 }}>
                  <View style={{ flexDirection: 'row', gap: 16, flexWrap: 'wrap' }}>
                    <SideDetail title="Call (CE)" side={selected.call} lotSize={d.lot_size} />
                    <SideDetail title="Put (PE)" side={selected.put} lotSize={d.lot_size} />
                  </View>
                  <NoticeBanner>
                    {d.lot_size
                      ? 'Premium for 1 lot excludes brokerage, taxes and slippage (charges calculator arrives with paper trading). '
                      : 'Required funds need the exchange lot size, which is not available in this mode. '}
                    The direction of the underlying is not the probability that an option position profits: theta, IV and spread also matter.
                  </NoticeBanner>
                </View>
              ) : null}
            </BottomSheet>
          </>
        )}
      </QueryState>
    </Screen>
  );
}
