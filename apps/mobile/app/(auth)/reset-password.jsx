import { useState } from 'react';
import { router, useLocalSearchParams } from 'expo-router';
import { AppText, Button, Card, Screen, TextField } from '../../components/ui';
import { describeError, useResetPassword } from '../../features/auth/api';
import { isSixDigitCode, passwordProblems } from '../../features/auth/validation';

export default function ResetPassword() {
  const { identifier = '' } = useLocalSearchParams();
  const [code, setCode] = useState('');
  const [password, setPassword] = useState('');
  const reset = useResetPassword();
  const pwProblem = password ? passwordProblems(password) : null;
  const canSubmit = isSixDigitCode(code) && password && !pwProblem;

  if (reset.isSuccess) {
    return (
      <Screen title="Password updated" edges={['bottom']} contentStyle={{ maxWidth: 480 }}>
        <Card>
          <AppText>{reset.data?.message}</AppText>
        </Card>
        <Button title="Sign in" fullWidth onPress={() => router.replace('/login')} />
      </Screen>
    );
  }

  return (
    <Screen title="Reset password" subtitle={`If an account exists for ${identifier}, a code was sent.`} keyboard edges={['bottom']} contentStyle={{ maxWidth: 480 }}>
      <TextField
        label="Reset code"
        value={code}
        onChangeText={(v) => setCode(v.replace(/\D/g, '').slice(0, 6))}
        keyboardType="number-pad"
        autoComplete="one-time-code"
        maxLength={6}
      />
      <TextField label="New password" value={password} onChangeText={setPassword} secure autoComplete="new-password" error={pwProblem} hint="At least 10 characters with a letter and a number." />
      {reset.error ? <AppText tone="negative" accessibilityRole="alert">{describeError(reset.error)}</AppText> : null}
      <Button
        title="Update password"
        fullWidth
        disabled={!canSubmit}
        loading={reset.isPending}
        onPress={() => reset.mutate({ identifier, code, new_password: password })}
      />
    </Screen>
  );
}
