import { router } from 'expo-router';
import { AppText, Card, ListRow, Screen } from '../../../components/ui';

export default function NewBot() {
  return (
    <Screen edges={['bottom']}>
      <AppText tone="muted">Every bot starts as a PAPER draft and must pass validation against your global risk limits.</AppText>
      <Card>
        <ListRow testID="new-ai" icon="sparkles-outline" title="AI Auto Create" subtitle="Answer a few questions and get a proposed configuration" onPress={() => router.push('/bots/ai-create')} />
        <ListRow testID="new-manual" icon="construct-outline" title="Manual Bot Builder" subtitle="Set every rule yourself" onPress={() => router.push('/bots/builder')} />
        <ListRow testID="new-template" icon="albums-outline" title="Strategy Templates" subtitle="Start from Momentum, Breakout, SMC and more" onPress={() => router.push('/bots/templates')} />
      </Card>
      <AppText variant="caption" tone="faint">To clone a bot, open it and choose Clone.</AppText>
    </Screen>
  );
}
