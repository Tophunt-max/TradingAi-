import { useState } from 'react';
import { View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { AppText, Badge, Button, Card, CardHeader, ConfirmDialog, KeyValue, NoticeBanner, QueryState, Screen, TextField } from '../../components/ui';
import { useTheme } from '../../components/theme/ThemeProvider';
import {
  STATUS_LABEL,
  TOKEN_WARNING,
  useBrokerHealth,
  useBrokerList,
  useBrokerPermissions,
  useConnectBroker,
  useDisconnectBroker,
  useSyncBroker,
} from '../../features/brokers/api';
import { formatIST } from '../../services/format';

function ConnectForm({ broker }) {
  const [token, setToken] = useState('');
  const connect = useConnectBroker();
  return (
    <View style={{ gap: 8 }} testID={`broker-connect-${broker.id}`}>
      <TextField
        label="Access token"
        secure
        autoCapitalize="none"
        autoCorrect={false}
        autoComplete="off"
        textContentType="none"
        importantForAutofill="no"
        value={token}
        onChangeText={setToken}
        placeholder="Paste the token from the broker"
        testID={`broker-token-${broker.id}`}
      />
      <AppText variant="caption" tone="muted" testID="broker-token-warning">{TOKEN_WARNING}</AppText>
      <Button
        title="Connect (read-only)"
        testID={`broker-connect-btn-${broker.id}`}
        disabled={!token.trim()}
        loading={connect.isPending}
        onPress={() => {
          const t = token.trim();
          setToken(''); // the token never stays in the app, whatever the outcome
          connect.mutate({ id: broker.id, token: t });
        }}
      />
      {connect.error ? <AppText tone="negative" testID="broker-connect-error">{connect.error.message}</AppText> : null}
    </View>
  );
}

function ConnectedPanel({ broker }) {
  const c = broker.connection;
  const [confirm, setConfirm] = useState(false);
  const sync = useSyncBroker();
  const disconnect = useDisconnectBroker();
  const health = useBrokerHealth(broker.id, false);
  return (
    <View style={{ gap: 4 }} testID={`broker-connected-${broker.id}`}>
      <KeyValue label="Last sync" value={c.last_sync_at ? formatIST(c.last_sync_at, { withDate: true }) : 'Never'} />
      <KeyValue label="Token valid until (at most)" value={formatIST(c.token_expires_at, { withDate: true })} />
      <KeyValue label="Permissions" value={c.scopes.join(', ') || '—'} />
      {health.data ? (
        <KeyValue label="Health" value={`${health.data.ok ? 'OK' : health.data.status}: ${health.data.detail}`} tone={health.data.ok ? 'positive' : 'negative'} />
      ) : null}
      {sync.data ? <AppText variant="caption" tone="muted" testID="broker-sync-result">{`Synced ${sync.data.counts.positions} position(s), ${sync.data.counts.orders} order(s), ${sync.data.counts.fills} fill(s).`}</AppText> : null}
      {sync.error ? <AppText tone="negative">{sync.error.message}</AppText> : null}
      <View style={{ flexDirection: 'row', gap: 8, flexWrap: 'wrap', marginTop: 4 }}>
        <Button title="Sync now" size="sm" testID={`broker-sync-${broker.id}`} loading={sync.isPending} onPress={() => sync.mutate(broker.id)} />
        <Button title="Check health" size="sm" variant="secondary" testID={`broker-health-${broker.id}`} loading={health.isFetching} onPress={() => health.refetch()} />
        <Button title="Disconnect" size="sm" variant="danger" testID={`broker-disconnect-${broker.id}`} onPress={() => setConfirm(true)} />
      </View>
      <ConfirmDialog
        visible={confirm}
        title={`Disconnect ${broker.name}?`}
        message="The token is deleted from the QuantAI server and real-account sync stops. This broker has no revoke API, so also revoke the token on the broker's access-token page."
        confirmLabel="Disconnect"
        destructive
        loading={disconnect.isPending}
        onCancel={() => setConfirm(false)}
        onConfirm={() => disconnect.mutate(broker.id, { onSettled: () => setConfirm(false) })}
      />
    </View>
  );
}

function BrokerCard({ b }) {
  const theme = useTheme();
  const status = STATUS_LABEL[b.status] ?? STATUS_LABEL.NOT_INTEGRATED;
  const integrated = b.kind === 'BROKER' && b.verification !== 'NOT_INTEGRATED';
  const connected = b.connection?.status === 'CONNECTED';
  return (
    <Card testID={`broker-${b.id}`}>
      <View style={{ flexDirection: 'row', gap: 12, alignItems: 'center' }}>
        <View style={{ width: 40, height: 40, borderRadius: 12, backgroundColor: theme.colors.surfaceRaised, alignItems: 'center', justifyContent: 'center' }}>
          <Ionicons name={b.kind === 'PAPER' ? 'flask-outline' : 'business-outline'} size={20} color={b.kind === 'PAPER' ? theme.colors.primary : theme.colors.textMuted} />
        </View>
        <View style={{ flex: 1 }}>
          <AppText variant="bodyStrong">{b.name}</AppText>
          <AppText variant="caption" tone="muted">{b.note}</AppText>
        </View>
        <Badge label={status.label} tone={status.tone} testID={`broker-status-${b.id}`} />
      </View>
      {integrated ? (
        <View style={{ gap: 8, marginTop: 8 }}>
          {b.verification === 'UNVERIFIED_AGAINST_LIVE_API' ? <Badge label="UNVERIFIED AGAINST LIVE API" tone="warning" testID={`broker-unverified-${b.id}`} /> : null}
          {b.capabilities ? (
            <AppText variant="caption" tone="muted">
              {`Implemented: ${b.capabilities.order_types.join('/')} orders, ${b.capabilities.product_types.join('/')}, cancel ${b.capabilities.cancel ? 'yes' : 'no'}, modify ${b.capabilities.modify ? 'yes' : 'no'}, streaming ${b.capabilities.streaming ? 'yes' : 'no'}. Orders stay locked.`}
            </AppText>
          ) : null}
          {b.connection?.last_error && !connected ? <AppText tone="negative">{b.connection.last_error}</AppText> : null}
          {connected ? <ConnectedPanel broker={b} /> : b.enabled ? <ConnectForm broker={b} /> : null}
        </View>
      ) : null}
    </Card>
  );
}

function Permissions() {
  const theme = useTheme();
  const q = useBrokerPermissions();
  return (
    <QueryState query={q}>
      {(p) => (
        <Card testID="trading-permissions">
          <CardHeader title="Trading permissions" subtitle={p.ready ? 'All checks passed.' : 'Live trading is locked. Every item below must pass first.'} />
          {p.checks.map((c) => (
            <View key={c.key} style={{ flexDirection: 'row', gap: 8, paddingVertical: 6 }} testID={`permission-${c.key}`}>
              <Ionicons name={c.passed ? 'checkmark-circle' : 'close-circle'} size={18} color={c.passed ? theme.colors.positive : theme.colors.negative} />
              <View style={{ flex: 1 }}>
                <AppText variant="bodyStrong">{c.label}</AppText>
                {c.detail ? <AppText variant="caption" tone="muted">{c.detail}</AppText> : null}
              </View>
            </View>
          ))}
        </Card>
      )}
    </QueryState>
  );
}

export default function Brokers() {
  const q = useBrokerList();
  return (
    <Screen edges={['bottom']}>
      <QueryState query={q}>{(rows) => rows.map((b) => <BrokerCard key={b.id} b={b} />)}</QueryState>
      <Permissions />
      <NoticeBanner>
        Broker tokens are stored only on the server, encrypted, and never shown in the app again. Connecting a broker is read-only: live trading stays locked until a verified adapter, regulatory checks and your explicit authorisation
        are all in place.
      </NoticeBanner>
    </Screen>
  );
}
