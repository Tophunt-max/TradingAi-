import { useEffect, useRef, useState } from 'react';
import { View } from 'react-native';
import { useLocalSearchParams } from 'expo-router';
import { AppText, Button, Screen, TextField } from '../../components/ui';
import { describeError, useResendCode, useVerify } from '../../features/auth/api';
import { isSixDigitCode } from '../../features/auth/validation';

const RESEND_SECONDS = 30;

export default function Verify() {
  const { identifier = '', resend } = useLocalSearchParams();
  const [code, setCode] = useState('');
  const [cooldown, setCooldown] = useState(RESEND_SECONDS);
  const verify = useVerify();
  const resendCode = useResendCode();
  const sentOnce = useRef(false);

  // Arriving from login with an unverified account: send a fresh code once.
  useEffect(() => {
    if (resend === '1' && identifier && !sentOnce.current) {
      sentOnce.current = true;
      resendCode.mutate(identifier);
    }
  }, [resend, identifier, resendCode]);

  useEffect(() => {
    if (cooldown <= 0) return undefined;
    const t = setTimeout(() => setCooldown((c) => c - 1), 1000);
    return () => clearTimeout(t);
  }, [cooldown]);

  const submit = () => {
    if (isSixDigitCode(code)) verify.mutate({ identifier, code });
  };

  return (
    <Screen title="Verify your account" subtitle={`Enter the 6-digit code sent to ${identifier}`} keyboard edges={['bottom']} contentStyle={{ maxWidth: 480 }}>
      <TextField
        testID="verify-code"
        label="Verification code"
        value={code}
        onChangeText={(v) => setCode(v.replace(/\D/g, '').slice(0, 6))}
        keyboardType="number-pad"
        autoComplete="one-time-code"
        textContentType="oneTimeCode"
        maxLength={6}
        onSubmitEditing={submit}
        style={{ letterSpacing: 6 }}
      />
      {verify.error ? <AppText tone="negative" accessibilityRole="alert">{describeError(verify.error)}</AppText> : null}
      <Button title="Verify" fullWidth disabled={!isSixDigitCode(code)} loading={verify.isPending} onPress={submit} testID="verify-submit" />
      <View style={{ alignItems: 'center' }}>
        <Button
          title={cooldown > 0 ? `Resend code in ${cooldown}s` : 'Resend code'}
          variant="ghost"
          size="sm"
          disabled={cooldown > 0 || resendCode.isPending}
          onPress={() => {
            resendCode.mutate(identifier);
            setCooldown(RESEND_SECONDS);
          }}
        />
        {resendCode.isSuccess ? <AppText variant="caption" tone="muted">{resendCode.data?.message}</AppText> : null}
      </View>
      <AppText variant="caption" tone="faint">
        Development builds print codes to the API server log. A production SMS/email provider is not integrated yet.
      </AppText>
    </Screen>
  );
}
