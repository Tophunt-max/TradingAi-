import { useState } from 'react';
import { View } from 'react-native';
import { useLocalSearchParams } from 'expo-router';
import { AppText, Button, Card, CardHeader, KeyValue, NoticeBanner, PnLText, Screen, TextField } from '../../components/ui';
import { useChargesEstimate } from '../../features/trading/api';
import { formatINR } from '../../services/format';

const ROWS = [
  ['brokerage', 'Brokerage'],
  ['stt', 'STT (sell side)'],
  ['exchange_txn', 'Exchange transaction'],
  ['sebi', 'SEBI turnover fee'],
  ['stamp_duty', 'Stamp duty (buy side)'],
  ['gst', 'GST (18%)'],
];

export default function ChargesCalculator() {
  const params = useLocalSearchParams();
  const [buy, setBuy] = useState(params.buy ? String(params.buy) : '100');
  const [sell, setSell] = useState(params.sell ? String(params.sell) : '110');
  const [qty, setQty] = useState(params.qty ? String(params.qty) : '');
  const est = useChargesEstimate();
  const r = est.data;
  const valid = Number(buy) > 0 && Number(sell) > 0 && Number.isInteger(Number(qty)) && Number(qty) >= 1;

  return (
    <Screen edges={['bottom']}>
      <Card>
        <CardHeader title="Option buy → sell" subtitle="NSE index options, intraday" />
        <TextField label="Buy premium (₹)" value={buy} onChangeText={setBuy} keyboardType="decimal-pad" testID="charges-buy" />
        <TextField label="Sell premium (₹)" value={sell} onChangeText={setSell} keyboardType="decimal-pad" testID="charges-sell" />
        <TextField label="Quantity (units = lots × lot size)" value={qty} onChangeText={setQty} keyboardType="number-pad" testID="charges-qty" />
        <AppText variant="caption" tone="faint">Enter units, not lots. Use the lot size shown on the option chain for the contract.</AppText>
        <Button title="Calculate" icon="calculator-outline" disabled={!valid} loading={est.isPending} onPress={() => est.mutate({ buy_price: buy, sell_price: sell, quantity: Number(qty) })} testID="charges-calc" />
        {est.error ? <AppText tone="negative">{est.error.message}</AppText> : null}
      </Card>
      {r ? (
        <Card testID="charges-result">
          <CardHeader title="Estimate" subtitle={`Schedule ${r.schedule_detail.id}`} />
          <View style={{ flexDirection: 'row', justifyContent: 'space-between' }}>
            <AppText tone="muted">Net P&L</AppText>
            <PnLText value={r.net_pnl} prefix="₹" variant="bodyStrong" />
          </View>
          <KeyValue label="Gross P&L" value={formatINR(r.gross_pnl)} />
          <KeyValue label="Total charges" value={formatINR(r.total.total)} />
          <KeyValue label="Break-even sell price" value={`₹${Number(r.break_even_price).toFixed(2)}`} />
          <AppText variant="label" tone="muted" style={{ marginTop: 8 }}>BREAKDOWN (BUY + SELL)</AppText>
          {ROWS.map(([k, label]) => (
            <KeyValue key={k} label={label} value={`${formatINR(r.buy[k])} + ${formatINR(r.sell[k])}`} />
          ))}
          <AppText variant="caption" tone="faint" style={{ marginTop: 6 }}>
            Rates: STT {r.schedule_detail.stt_sell_pct}% sell · NSE txn {r.schedule_detail.exchange_txn_pct}% · SEBI {r.schedule_detail.sebi_pct}% · stamp {r.schedule_detail.stamp_buy_pct}% buy · GST {r.schedule_detail.gst_pct}% · brokerage ₹{r.brokerage_per_order}/order (configurable). Effective from {r.schedule_detail.effective_from}.
          </AppText>
        </Card>
      ) : null}
      <NoticeBanner>
        Estimates only. Not modelled: IPFT charge and STT on exercised in-the-money options held to expiry. Always reconcile with your broker&apos;s contract note.
      </NoticeBanner>
    </Screen>
  );
}
