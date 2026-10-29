import { AppText, Screen } from '../../../components/ui';
import { PlannedFeature } from '../../../components/PlannedFeature';

export default function Bots() {
  return (
    <Screen title="Bots" subtitle="Create, validate and manage trading bots">
      <AppText tone="muted">
        Bot status will always reflect the backend execution state. All bots share one global risk budget and start in paper mode.
      </AppText>
      <PlannedFeature id="bot-builder" />
      <PlannedFeature id="risk-center" />
    </Screen>
  );
}
