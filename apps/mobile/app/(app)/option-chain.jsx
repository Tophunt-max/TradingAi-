import { useState } from 'react';
import { Pressable, View } from 'react-native';
import { router, useLocalSearchParams } from 'expo-router';
import { AppText, Badge, BottomSheet, Button, Card, ChipRow, DataSourceBadge, KeyValue, NoticeBanner, QueryState, Screen, SegmentedControl, StatTile, TextField } from '../../components/ui';
import { useTheme } from '../../components/theme/ThemeProvider';
import { useExpiries, useOptionChain } from '../../features/market/api';
import { isStale } from '../../features/system/api';
import { fixed, formatINR, formatIST, formatPrice } from '../../services/format';
import { QualityBanner } from '../../features/market/QualityBanner';
import { ORDER_TONE } from '../../features/portfolio/Lists';
import { newIdempotencyKey, usePaperEntry } from '../../features/trading/api';
import { OptionsIntelSection } from '../../features/ai/OptionsIntel';

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

function PaperBuy({ underlying, expiry, row, lotSize, source, onLeave }) {
  const entry = usePaperEntry();
  const [side, setSide] = useState('CE');
  const [lots, setLots] = useState('1');
  const [sl, setSl] = useState('20');
  const [target, setTarget] = useState('30');
  // One key per intended order: a retry after a network error returns the same order.
  const [key, setKey] = useState(newIdempotencyKey);
  const leg = side === 'CE' ? row.call : row.put;
  const o = entry.data;
  const submit = () =>
    entry.mutate(
      { underlying, option_type: side, expiry, strike: row.strike, lots: Number(lots), stop_loss_pct: sl, target_pct: target || null, idempotency_key: key },
      { onSuccess: () => setKey(newIdempotencyKey()) },
    );
  const valid = Number.isInteger(Number(lots)) && Number(lots) >= 1 && Number(sl) > 0 && Number(sl) < 100 && (!target || Number(target) > 0);
  return (
    <Card testID="paper-buy">
      <AppText variant="bodyStrong">Paper buy</AppText>
      <SegmentedControl value={side} onChange={(v) => { setSide(v); entry.reset(); }} options={[{ value: 'CE', label: 'Buy CE' }, { value: 'PE', label: 'Buy PE' }]} testID="paper-side" />
      <View style={{ flexDirection: 'row', gap: 8 }}>
        <TextField style={{ flex: 1 }} label="Lots" value={lots} onChangeText={setLots} keyboardType="number-pad" testID="paper-lots" />
        <TextField style={{ flex: 1 }} label="Stop-loss %" value={sl} onChangeText={setSl} keyboardType="decimal-pad" testID="paper-sl" />
        <TextField style={{ flex: 1 }} label="Target %" value={target} onChangeText={setTarget} keyboardType="decimal-pad" testID="paper-target" />
      </View>
      <AppText variant="caption" tone="faint">
        {leg?.ask ? `Market order: fills at the ask (₹${fixed(leg.ask)}) plus 1 tick of slippage. ` : 'No ask price right now: the risk manager will reject a market buy. '}
        {lotSize ? `1 lot = ${lotSize} units.` : source === 'DEMO' ? 'DEMO data: 1 lot = 1 unit (placeholder, not the NSE lot size).' : 'Lot size unavailable: the order will be rejected.'}
        {' '}Global risk limits apply.
      </AppText>
      <Button title={`Place paper BUY ${side}`} icon="cart-outline" disabled={!valid || !leg} loading={entry.isPending} onPress={submit} testID="paper-submit" />
      {entry.error ? <NoticeBanner tone="danger" testID="paper-error">{entry.error.message}</NoticeBanner> : null}
      {o ? (
        <View style={{ gap: 6 }} testID="paper-result">
          <View style={{ flexDirection: 'row', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
            <Badge label={o.status} tone={ORDER_TONE[o.status]} testID="paper-result-status" />
            <AppText variant="caption">{o.instrument}{o.avg_fill_price ? ` · filled ${o.filled_qty} @ ₹${fixed(o.avg_fill_price)}` : ''}</AppText>
          </View>
          {o.reject_reason ? <AppText variant="caption" tone="negative">{o.reject_reason}</AppText> : null}
          <Button title="View order" size="sm" variant="ghost" onPress={() => { onLeave(); router.push(`/orders/${o.id}`); }} testID="paper-view-order" />
        </View>
      ) : null}
    </Card>
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
                    accessibilityLabel={`Strike ${r.strike}${r.is_atm ? " ATM" : ""} details`}
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
                  <OptionsIntelSection key={`intel-${selected.strike}`} underlying={underlying} expiry={d.expiry} strike={selected.strike} />
                  <PaperBuy key={selected.strike} underlying={underlying} expiry={d.expiry} row={selected} lotSize={d.lot_size} source={d.source} onLeave={() => setSelected(null)} />
                  <Button title="Charges calculator" icon="calculator-outline" size="sm" variant="ghost" onPress={() => { setSelected(null); router.push({ pathname: '/charges', params: { buy: selected.call?.ask ?? '', qty: d.lot_size ?? '' } }); }} />
                  <NoticeBanner>
                    {d.lot_size
                      ? 'Premium for 1 lot excludes brokerage, taxes and slippage; see the charges calculator. '
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
