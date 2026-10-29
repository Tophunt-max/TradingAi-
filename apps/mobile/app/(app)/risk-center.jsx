import { useState } from 'react';
import { View } from 'react-native';
import { AppText, BottomSheet, Button, Card, CardHeader, ConfirmDialog, KeyValue, NoticeBanner, ProgressRing, QueryState, Screen, TextField, ToggleRow } from '../../components/ui';
import { describeError } from '../../features/auth/api';
import { useKillSwitch, useRisk, useUpdateRiskLimits } from '../../features/trading/api';
import { useHaptics } from '../../hooks/useHaptics';
import { formatINR, formatIST } from '../../services/format';

const EDITABLE = [
  ['capital', 'Capital (₹)', 'decimal-pad'],
  ['max_risk_per_trade', 'Max risk per trade (₹)', 'decimal-pad'],
  ['daily_loss_limit', 'Daily loss stop (₹)', 'decimal-pad'],
  ['daily_profit_lock', 'Daily net profit lock (₹)', 'decimal-pad'],
  ['max_entries_per_day', 'Max entries per day', 'number-pad'],
  ['max_open_positions', 'Max open positions (all bots)', 'number-pad'],
  ['max_lots_per_trade', 'Max lots per trade', 'number-pad'],
  ['square_off_time_ist', 'Square-off time (IST)', 'default'],
];

function EditLimits({ limits, onClose }) {
  const update = useUpdateRiskLimits();
  const [form, setForm] = useState(() => Object.fromEntries(EDITABLE.map(([k]) => [k, String(limits[k] == null ? '' : Number.isNaN(Number(limits[k])) ? limits[k] : Number(limits[k]))])));
  const [reEntry, setReEntry] = useState(limits.re_entry_after_stop_loss);
  const submit = () => {
    const patch = { re_entry_after_stop_loss: reEntry };
    EDITABLE.forEach(([k, , kb]) => {
      if (form[k] === '') return;
      patch[k] = kb === 'number-pad' ? Number(form[k]) : form[k];
    });
    update.mutate(patch, { onSuccess: onClose });
  };
  const err = update.error;
  return (
    <View style={{ gap: 10 }}>
      {EDITABLE.map(([k, label, kb]) => (
        <TextField key={k} label={label} value={form[k]} keyboardType={kb} onChangeText={(v) => setForm((f) => ({ ...f, [k]: v }))} />
      ))}
      <ToggleRow title="Allow re-entry after stop-loss" value={reEntry} onValueChange={setReEntry} />
      {err ? <AppText tone="negative">{Array.isArray(err.details) && typeof err.details[0] === 'string' ? err.details.join(' ') : describeError(err)}</AppText> : null}
      <Button title="Save limits" loading={update.isPending} onPress={submit} testID="save-limits" />
      <AppText variant="caption" tone="faint">Only you can change these limits. Bots and AI components can read them but never modify them.</AppText>
    </View>
  );
}

export default function RiskCenter() {
  const risk = useRisk();
  const kill = useKillSwitch();
  const { notify } = useHaptics();
  const [editing, setEditing] = useState(false);
  // Target state captured when the dialog opens, so its text cannot change mid-request.
  const [confirm, setConfirm] = useState({ engage: true });
  const [confirmOpen, setConfirmOpen] = useState(false);

  return (
    <Screen edges={['bottom']} refreshing={risk.isRefetching} onRefresh={risk.refetch}>
      <QueryState query={risk}>
        {(r) => {
          const l = r.limits;
          const used = Number(l.daily_loss_limit) ? (Math.max(-Number(r.usage.realized_pnl), 0) / Number(l.daily_loss_limit)) * 100 : 0;
          return (
            <>
              <Card style={{ alignItems: 'center', gap: 8 }}>
                <ProgressRing percent={used} sublabel="Daily loss used" />
                <AppText variant="caption" tone="muted">
                  {formatINR(Math.max(-Number(r.usage.realized_pnl), 0))} of {formatINR(l.daily_loss_limit)}
                </AppText>
              </Card>
              {!r.engine_active ? <NoticeBanner tone="info">{r.engine_note}</NoticeBanner> : null}
              <Card>
                <CardHeader title="Global limits" subtitle="Shared by every bot" right={<Button title="Edit" size="sm" variant="ghost" onPress={() => setEditing(true)} testID="edit-limits" />} />
                <KeyValue label="Capital" value={formatINR(l.capital)} />
                <KeyValue label="Max risk per trade" value={formatINR(l.max_risk_per_trade)} />
                <KeyValue label="Daily loss stop" value={formatINR(l.daily_loss_limit)} />
                <KeyValue label="Daily profit lock" value={formatINR(l.daily_profit_lock)} />
                <KeyValue label="Entries used" value={`${r.usage.entries_used} / ${l.max_entries_per_day}`} />
                <KeyValue label="Open positions" value={`${r.usage.open_positions} / ${l.max_open_positions}`} />
                <KeyValue label="Max lots per trade" value={l.max_lots_per_trade} />
                <KeyValue label="Re-entry after SL" value={l.re_entry_after_stop_loss ? 'Allowed' : 'Disabled'} />
                <KeyValue label="Square-off" value={`${l.square_off_time_ist} IST`} />
              </Card>
              <Card>
                <CardHeader title="Exposure & costs" />
                <KeyValue label="Realised P&L" value={formatINR(r.usage.realized_pnl)} />
                <KeyValue label="Unrealised P&L" value={formatINR(r.usage.unrealized_pnl)} />
                <KeyValue label="Planned risk" value={formatINR(r.usage.planned_risk)} />
                <KeyValue label="Estimated charges" value={formatINR(r.usage.estimated_charges)} />
                <KeyValue label="Profit lock reached" value={r.usage.profit_lock_reached ? 'Yes' : 'No'} />
              </Card>
              <Card>
                <CardHeader title="New entries" />
                <AppText tone={r.new_entries_allowed ? 'positive' : 'warning'}>{r.new_entries_allowed ? 'Allowed' : 'Blocked'}</AppText>
                {r.block_reasons.map((b) => <AppText key={b} variant="caption" tone="muted">• {b}</AppText>)}
              </Card>
              <Button
                testID="kill-switch"
                title={l.kill_switch_engaged ? 'Release kill switch' : 'Kill switch'}
                icon={l.kill_switch_engaged ? 'lock-open-outline' : 'power'}
                variant={l.kill_switch_engaged ? 'secondary' : 'danger'}
                fullWidth
                onPress={() => {
                  setConfirm({ engage: !l.kill_switch_engaged });
                  setConfirmOpen(true);
                }}
              />
              {l.kill_switch_changed_at ? (
                <AppText variant="caption" tone="faint" style={{ textAlign: 'center' }}>
                  Kill switch {l.kill_switch_engaged ? 'engaged' : 'released'} {formatIST(l.kill_switch_changed_at, { withDate: true })} IST
                </AppText>
              ) : null}
              <AppText variant="caption" tone="faint">{r.disclaimer}</AppText>
              <BottomSheet visible={editing} onClose={() => setEditing(false)} title="Edit global limits">
                <EditLimits limits={l} onClose={() => setEditing(false)} />
              </BottomSheet>
              <ConfirmDialog
                visible={confirmOpen}
                title={confirm.engage ? 'Engage kill switch?' : 'Release kill switch?'}
                message={
                  confirm.engage
                    ? 'This blocks all new entries for every bot. Once execution exists it will also cancel pending entry orders and manage open positions safely.'
                    : 'New entries will be allowed again once all other checks pass.'
                }
                confirmLabel={confirm.engage ? 'Engage' : 'Release'}
                destructive={confirm.engage}
                loading={kill.isPending}
                onCancel={() => setConfirmOpen(false)}
                onConfirm={() =>
                  kill.mutate(confirm.engage, {
                    onSuccess: () => notify('warning'),
                    onSettled: () => setConfirmOpen(false),
                  })
                }
              />
            </>
          );
        }}
      </QueryState>
    </Screen>
  );
}
