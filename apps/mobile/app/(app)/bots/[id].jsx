import { useEffect, useState } from 'react';
import { View } from 'react-native';
import { Stack, router, useLocalSearchParams } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import { AppText, Badge, Button, Card, CardHeader, ConfirmDialog, ModeBadge, NoticeBanner, QueryState, Screen, StatTile } from '../../../components/ui';
import { useTheme } from '../../../components/theme/ThemeProvider';
import { ConfigSummary } from '../../../features/bots/ConfigSummary';
import { useBot, useBotAction, useValidateBot } from '../../../features/trading/api';
import { formatIST } from '../../../services/format';

const ICON = { PASS: ['checkmark-circle', 'positive'], FAIL: ['close-circle', 'negative'], WARN: ['alert-circle', 'warning'] };

function ValidationCard({ report }) {
  const theme = useTheme();
  return (
    <Card testID="validation-report">
      <CardHeader title="Review & validate" subtitle={`Version ${report.version}`} right={<Badge label={report.can_activate ? 'READY' : 'BLOCKED'} tone={report.can_activate ? 'positive' : 'negative'} />} />
      {report.checks.map((c) => {
        const [icon, tone] = ICON[c.status];
        return (
          <View key={c.key} style={{ flexDirection: 'row', gap: 10, paddingVertical: 8, borderBottomWidth: 1, borderColor: theme.colors.borderSubtle }}>
            <Ionicons name={icon} size={18} color={theme.colors[tone]} style={{ marginTop: 2 }} />
            <View style={{ flex: 1 }}>
              <AppText variant="bodyStrong">{c.label}</AppText>
              <AppText variant="caption" tone="muted">{c.message}</AppText>
            </View>
          </View>
        );
      })}
    </Card>
  );
}

export default function BotDetail() {
  const { id, validate: autoValidate } = useLocalSearchParams();
  const bot = useBot(id);
  const action = useBotAction();
  const validate = useValidateBot();
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [notice, setNotice] = useState(null);

  useEffect(() => {
    if (autoValidate === '1' && id) validate.mutate(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoValidate, id]);

  const run = (name) =>
    action.mutate(
      { id, action: name },
      {
        onError: (e) => setNotice(e.message),
        onSuccess: (data) => {
          if (name === 'clone') router.replace(`/bots/${data.id}`);
        },
      },
    );

  return (
    <Screen edges={['bottom']} refreshing={bot.isRefetching} onRefresh={bot.refetch}>
      <QueryState query={bot}>
        {(b) => (
          <>
            <Stack.Screen options={{ title: b.name }} />
            <View style={{ flexDirection: 'row', gap: 6, flexWrap: 'wrap' }}>
              <Badge label={b.status} tone={b.status === 'DRAFT' ? 'neutral' : 'positive'} />
              <ModeBadge live={b.mode === 'LIVE'} />
              <Badge label={b.creation_method === 'AI' ? 'AI GENERATED' : b.creation_method} tone={b.creation_method === 'AI' ? 'info' : 'neutral'} />
              <Badge label={`v${b.current_version}`} />
            </View>
            <AppText variant="caption" tone="faint">
              {b.underlying} · {b.strategy_name} · {b.timeframe} · updated {formatIST(b.updated_at, { withDate: true })} IST
            </AppText>

            <View style={{ flexDirection: 'row', gap: 10, flexWrap: 'wrap' }}>
              <StatTile label="Today P&L" value={b.today_pnl ?? '—'} />
              <StatTile label="Net P&L" value={b.total_net_pnl ?? '—'} />
              <StatTile label="Trades" value={b.trade_count} sub={b.last_signal ?? 'No signals yet'} />
            </View>

            <View style={{ flexDirection: 'row', gap: 8, flexWrap: 'wrap' }}>
              <Button title="Start" icon="play" size="sm" onPress={() => run('start')} testID="bot-start" />
              <Button title="Pause" icon="pause" size="sm" variant="secondary" onPress={() => run('pause')} />
              <Button title="Stop" icon="stop" size="sm" variant="danger" onPress={() => run('stop')} />
            </View>
            {notice ? <NoticeBanner tone="danger" testID="bot-notice">{notice}</NoticeBanner> : null}

            {validate.data ? (
              <ValidationCard report={validate.data} />
            ) : (
              <Button title="Run validation" icon="shield-checkmark-outline" variant="secondary" fullWidth loading={validate.isPending} onPress={() => validate.mutate(id)} testID="bot-validate" />
            )}
            {validate.error ? <AppText tone="negative">{validate.error.message}</AppText> : null}

            <ConfigSummary config={b.config} strategyName={b.strategy_name} />

            <View style={{ flexDirection: 'row', gap: 8, flexWrap: 'wrap' }}>
              <Button title="Edit" icon="create-outline" size="sm" variant="secondary" onPress={() => router.push({ pathname: '/bots/builder', params: { id } })} />
              <Button title="Clone" icon="copy-outline" size="sm" variant="secondary" onPress={() => run('clone')} />
              <Button title="Delete" icon="trash-outline" size="sm" variant="danger" onPress={() => setConfirmDelete(true)} />
            </View>
            <Card>
              <CardHeader title="Logs & trade history" />
              <AppText tone="muted">Execution logs and trades appear once the bot can run (Phase 6). Configuration changes are in your audit history.</AppText>
            </Card>
            <ConfirmDialog
              visible={confirmDelete}
              title="Delete bot?"
              message={`"${b.name}" and all ${b.versions.length} configuration versions will be deleted.`}
              confirmLabel="Delete"
              destructive
              loading={action.isPending}
              onCancel={() => setConfirmDelete(false)}
              onConfirm={() => action.mutate({ id, action: 'delete' }, { onSuccess: () => router.replace('/bots'), onSettled: () => setConfirmDelete(false) })}
            />
          </>
        )}
      </QueryState>
    </Screen>
  );
}
