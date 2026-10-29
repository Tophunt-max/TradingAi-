import { useState } from 'react';
import { router } from 'expo-router';
import { AppText, Badge, Button, Card, CardHeader, ConfirmDialog, Divider, ListRow, QueryState, Screen, Select, TextField, ToggleRow } from '../../../components/ui';
import { describeError } from '../../../features/auth/api';
import { usePreferences, useUpdatePreferences, useUpdateProfile } from '../../../features/account/api';
import { useAuthStore } from '../../../store/auth';

const NOTIFICATION_LABELS = {
  ai_signals: 'New AI signals',
  bot_status: 'Bot started / paused / stopped',
  order_updates: 'Order submitted / filled / rejected',
  risk_alerts: 'Risk limit reached',
  data_alerts: 'Stale market data',
  broker_alerts: 'Broker disconnection',
  daily_report: 'Daily report',
};

function ProfileCard() {
  const user = useAuthStore((s) => s.user);
  const [name, setName] = useState(user?.full_name ?? '');
  const update = useUpdateProfile();
  return (
    <Card>
      <CardHeader title="Profile" right={<Badge label={user?.role?.toUpperCase() ?? 'USER'} tone="neutral" />} />
      <TextField label="Full name" value={name} onChangeText={setName} />
      <AppText variant="caption" tone="muted" style={{ marginTop: 8 }}>
        {[user?.email, user?.phone].filter(Boolean).join(' · ')}
      </AppText>
      {update.error ? <AppText tone="negative">{describeError(update.error)}</AppText> : null}
      <Button
        title={update.isSuccess && name === user?.full_name ? 'Saved' : 'Save profile'}
        size="sm"
        variant="secondary"
        disabled={name.trim().length < 2 || name.trim() === user?.full_name}
        loading={update.isPending}
        onPress={() => update.mutate({ full_name: name.trim() })}
      />
    </Card>
  );
}

function PreferencesCard() {
  const query = usePreferences();
  const update = useUpdatePreferences();
  return (
    <QueryState query={query}>
      {(prefs) => (
        <>
          <Card>
            <CardHeader title="App preferences" />
            <Select
              label="Theme"
              value={prefs.theme}
              onChange={(theme) => update.mutate({ theme })}
              options={[
                { value: 'dark', label: 'Dark (recommended)' },
                { value: 'light', label: 'Light' },
                { value: 'system', label: 'Follow system' },
              ]}
            />
            <Select
              label="Language"
              value={prefs.language}
              onChange={(language) => update.mutate({ language })}
              options={[
                { value: 'en', label: 'English' },
                { value: 'hi', label: 'हिन्दी (Hindi)' },
                { value: 'hinglish', label: 'Hinglish' },
              ]}
            />
            <AppText variant="caption" tone="faint">Hindi and Hinglish UI translations are not complete yet.</AppText>
            <ToggleRow title="Reduce motion" value={prefs.reduced_motion} onValueChange={(v) => update.mutate({ reduced_motion: v })} />
            <ToggleRow title="Haptic feedback" value={prefs.haptics_enabled} onValueChange={(v) => update.mutate({ haptics_enabled: v })} />
          </Card>
          <Card>
            <CardHeader title="Notifications" subtitle="Delivery channels are added with the alerts module (Phase 5)." />
            {Object.entries(NOTIFICATION_LABELS).map(([key, label]) => (
              <ToggleRow
                key={key}
                title={label}
                value={prefs.notifications?.[key] !== false}
                onValueChange={(v) => update.mutate({ notifications: { [key]: v } })}
              />
            ))}
          </Card>
          {update.error ? <AppText tone="negative">{describeError(update.error)}</AppText> : null}
        </>
      )}
    </QueryState>
  );
}

export default function Settings() {
  const logout = useAuthStore((s) => s.logout);
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  return (
    <Screen edges={['bottom']}>
      <ProfileCard />
      <PreferencesCard />
      <Card>
        <CardHeader title="Security & trading" />
        <ListRow title="Account" subtitle="Demo balance, ledger and reset" icon="wallet-outline" onPress={() => router.push('/account')} testID="settings-account" />
        <ListRow title="Change password" icon="key-outline" onPress={() => router.push('/settings/security')} />
        <ListRow title="Active sessions" icon="phone-portrait-outline" onPress={() => router.push('/settings/sessions')} />
        <ListRow title="Audit history" icon="list-outline" onPress={() => router.push('/settings/audit')} />
        <ListRow title="Broker connections" icon="link-outline" onPress={() => router.push('/feature/brokers')} />
        <ListRow title="Trading permissions" subtitle="Paper only · live trading locked" icon="lock-closed-outline" />
        <ListRow title="System health" icon="pulse-outline" onPress={() => router.push('/system-health')} />
        <ListRow title="Help & Support" icon="help-circle-outline" onPress={() => router.push('/help')} />
        <Divider />
        <ListRow title="Log out" icon="log-out-outline" destructive onPress={() => setConfirm(true)} testID="logout" />
      </Card>
      <ConfirmDialog
        visible={confirm}
        title="Log out?"
        message="This signs you out on this device and revokes its session."
        confirmLabel="Log out"
        destructive
        loading={busy}
        onCancel={() => setConfirm(false)}
        onConfirm={async () => {
          setBusy(true);
          await logout();
        }}
      />
    </Screen>
  );
}
