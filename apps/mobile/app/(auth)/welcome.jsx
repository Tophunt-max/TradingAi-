import { View } from 'react-native';
import { router } from 'expo-router';
import { AppText, Button, Screen } from '../../components/ui';
import { BrandMark } from '../../features/auth/BrandMark';

export default function Welcome() {
  return (
    <Screen contentStyle={{ flexGrow: 1, justifyContent: 'center', minHeight: 560, maxWidth: 480 }}>
      <View style={{ alignItems: 'center', gap: 12, marginBottom: 24 }}>
        <BrandMark size={80} />
        <AppText variant="display">{'QuantAI F&O'}</AppText>
        <AppText tone="muted" style={{ textAlign: 'center' }}>
          Smarter Analysis. Better Decisions. Higher Consistency.
        </AppText>
      </View>
      <Button title="Sign in" fullWidth onPress={() => router.push('/login')} testID="welcome-login" />
      <Button title="Create account" variant="secondary" fullWidth onPress={() => router.push('/register')} testID="welcome-register" />
      <AppText variant="caption" tone="faint" style={{ textAlign: 'center', marginTop: 16 }}>
        Analysis tools only. Trading in derivatives involves substantial risk of loss. No profits or prediction accuracy are guaranteed.
      </AppText>
    </Screen>
  );
}
