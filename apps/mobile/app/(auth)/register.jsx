import { useState } from 'react';
import { router } from 'expo-router';
import { AppText, Button, Card, SegmentedControl, Screen, TextField, ToggleRow } from '../../components/ui';
import { describeError, useRegister } from '../../features/auth/api';
import { passwordProblems } from '../../features/auth/validation';

export default function Register() {
  const [method, setMethod] = useState('email');
  const [fullName, setFullName] = useState('');
  const [identifier, setIdentifier] = useState('');
  const [password, setPassword] = useState('');
  const [accepted, setAccepted] = useState(false);
  const register = useRegister();
  const pwProblem = password ? passwordProblems(password) : null;

  const canSubmit = fullName.trim().length >= 2 && identifier.trim() && password && !pwProblem && accepted;

  const submit = () => {
    if (!canSubmit) return;
    const body = {
      full_name: fullName.trim(),
      password,
      accept_risk_disclosure: accepted,
      [method]: identifier.trim(),
    };
    register.mutate(body, {
      onSuccess: () => router.push({ pathname: '/verify', params: { identifier: identifier.trim() } }),
    });
  };

  return (
    <Screen title="Create account" subtitle="Paper trading is enabled by default" keyboard edges={['bottom']} contentStyle={{ maxWidth: 480 }}>
      <TextField label="Full name" value={fullName} onChangeText={setFullName} autoComplete="name" leftIcon="person-outline" testID="register-name" />
      <SegmentedControl
        value={method}
        onChange={(m) => {
          setMethod(m);
          setIdentifier('');
        }}
        options={[
          { value: 'email', label: 'Email' },
          { value: 'phone', label: 'Mobile' },
        ]}
      />
      <TextField
        testID="register-identifier"
        label={method === 'email' ? 'Email' : 'Mobile number'}
        value={identifier}
        onChangeText={setIdentifier}
        keyboardType={method === 'email' ? 'email-address' : 'phone-pad'}
        autoCapitalize="none"
        leftIcon={method === 'email' ? 'mail-outline' : 'call-outline'}
      />
      <TextField
        testID="register-password"
        label="Password"
        value={password}
        onChangeText={setPassword}
        secure
        autoComplete="new-password"
        leftIcon="lock-closed-outline"
        error={pwProblem}
        hint="At least 10 characters with a letter and a number."
      />
      <Card>
        <AppText variant="bodyStrong">Risk disclosure</AppText>
        <AppText variant="caption" tone="muted" style={{ marginTop: 6 }}>
          QuantAI F&amp;O provides analysis and simulation tools. Options and futures trading can lose more than you expect, quickly. AI
          predictions can be wrong. Paper and backtest results are not live profits and do not guarantee future returns.
        </AppText>
        <ToggleRow title="I understand and accept these risks" value={accepted} onValueChange={setAccepted} />
      </Card>
      {register.error ? <AppText tone="negative" accessibilityRole="alert">{describeError(register.error)}</AppText> : null}
      <Button title="Create account" fullWidth disabled={!canSubmit} loading={register.isPending} onPress={submit} testID="register-submit" />
    </Screen>
  );
}
