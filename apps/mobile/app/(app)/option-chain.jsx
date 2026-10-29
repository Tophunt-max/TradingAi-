import { useState } from 'react';
import { Pressable, View } from 'react-native';
import { useLocalSearchParams } from 'expo-router';
import { AppText, BottomSheet, Card, ChipRow, DataSourceBadge, KeyValue, NoticeBanner, QueryState, Screen, SegmentedControl, StatTile } from '../../components/ui';
import { useTheme } from '../../components/theme/ThemeProvider';
import { useExpiries, useOptionChain } from '../../features/market/api';
import { isStale } from '../../features/system/api';
import { formatIST } from '../../services/format';

const fmtOI = (n) => (n >= 1e5 ? `${(n / 1e5).toFixed(1)}L` : n.toLocaleString('en-IN'));
const fmtDate = (iso) => new Date(`${iso}T00:00:00Z`).toLocaleDateString('en-IN', { day: '2-digit', month: 'short', timeZone: 'UTC' });

function Cell({ children, flex = 1, align = 'center', strong, tone }) {
  return (
    <View style={{ flex, alignItems: align === 'center' ? 'center' : align === 'right' ? 'flex-end' : 'flex-start' }}>
      <AppText variant={strong ? 'bodyStrong' : 'caption'} tone={tone} style={{ fontVariant: ['tabular-nums'] }}>{children}</AppText>
    </View>
  );
}

function SideDetail({ title, side }) {
  return (
    <View style={{ flex: 1, minWidth: 150 }}>
      <AppText variant="bodyStrong">{title}</AppText>
      <KeyValue label="LTP" value={side.ltp.toFixed(2)} />
      <KeyValue label="Bid / Ask" value={`${side.bid.toFixed(2)} / ${side.ask.toFixed(2)}`} />
      <KeyValue label="Spread" value={`${(((side.ask - side.bid) / side.ltp) * 100).toFixed(2)}%`} tone={(side.ask - side.bid) / side.ltp > 0.02 ? 'warning' : undefined} />
      <KeyValue label="IV" value={`${side.iv.toFixed(2)}%`} />
      <KeyValue label="Delta" value={side.delta.toFixed(3)} />
      <KeyValue label="Gamma" value={side.gamma.toFixed(5)} />
      <KeyValue label="Theta / day" value={side.theta.toFixed(2)} />
      <KeyValue label="Vega" value={side.vega.toFixed(2)} />
      <KeyValue label="OI (chg)" value={`${fmtOI(side.oi)} (${side.oi_change >= 0 ? '+' : ''}${fmtOI(side.oi_change)})`} />
      <KeyValue label="Volume" value={fmtOI(side.volume)} />
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
              <StatTile label="Spot" value={d.spot.toLocaleString('en-IN', { minimumFractionDigits: 2 })} sub={`ATM ${d.atm_strike}`} />
              <StatTile label="PCR (OI)" value={d.pcr ?? '—'} sub={`Lot size: ${d.lot_size ?? 'unavailable'}`} />
            </View>
            <NoticeBanner testID="chain-metadata-note">{d.metadata_note}</NoticeBanner>
            <SegmentedControl value={view} onChange={setView} options={[{ value: 'LTP', label: 'LTP & OI' }, { value: 'GREEKS', label: 'IV & Delta' }]} />
            <Card padded={false} testID="option-chain-table">
              <View style={{ flexDirection: 'row', justifyContent: 'space-between', padding: 12, paddingBottom: 4 }}>
                <AppText variant="label" tone="positive">CALLS (CE)</AppText>
                <DataSourceBadge source={d.source} stale={isStale(d.as_of)} />
                <AppText variant="label" tone="negative">PUTS (PE)</AppText>
              </View>
              <View style={{ flexDirection: 'row', paddingHorizontal: 8, paddingVertical: 6, borderBottomWidth: 1, borderColor: theme.colors.borderSubtle }}>
                {(view === 'LTP' ? ['OI', 'LTP', 'STRIKE', 'LTP', 'OI'] : ['IV', 'Δ', 'STRIKE', 'Δ', 'IV']).map((h, i) => (
                  <Cell key={i} flex={i === 2 ? 1.2 : 1}><AppText variant="label" tone="faint">{h}</AppText></Cell>
                ))}
              </View>
              {d.rows.map((r) => {
                const callItm = r.strike < d.spot;
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
                          <Cell>{fmtOI(r.call.oi)}</Cell>
                          <Cell strong>{r.call.ltp.toFixed(2)}</Cell>
                        </>
                      ) : (
                        <>
                          <Cell>{r.call.iv.toFixed(1)}</Cell>
                          <Cell strong>{r.call.delta.toFixed(2)}</Cell>
                        </>
                      )}
                    </View>
                    <Cell flex={1.2} strong tone={r.is_atm ? 'primary' : undefined}>{r.strike}</Cell>
                    <View style={{ flex: 2, flexDirection: 'row', backgroundColor: !callItm ? 'rgba(239,68,68,0.06)' : 'transparent', borderRadius: 4 }}>
                      {view === 'LTP' ? (
                        <>
                          <Cell strong>{r.put.ltp.toFixed(2)}</Cell>
                          <Cell>{fmtOI(r.put.oi)}</Cell>
                        </>
                      ) : (
                        <>
                          <Cell strong>{r.put.delta.toFixed(2)}</Cell>
                          <Cell>{r.put.iv.toFixed(1)}</Cell>
                        </>
                      )}
                    </View>
                  </Pressable>
                );
              })}
            </Card>
            <AppText variant="caption" tone="faint">
              Greeks: {d.greeks_model}. As of {formatIST(d.as_of)} IST. Shaded cells are in the money.
            </AppText>
            <BottomSheet visible={!!selected} onClose={() => setSelected(null)} title={selected ? `${underlying} ${selected.strike} · ${fmtDate(d.expiry)}` : ''}>
              {selected ? (
                <View style={{ gap: 12 }}>
                  <View style={{ flexDirection: 'row', gap: 16, flexWrap: 'wrap' }}>
                    <SideDetail title="Call (CE)" side={selected.call} />
                    <SideDetail title="Put (PE)" side={selected.put} />
                  </View>
                  <NoticeBanner>
                    Required funds and charges need the exchange lot size, which is not available yet. The direction of the underlying is not the
                    probability that an option position profits: theta, IV and spread also matter.
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
