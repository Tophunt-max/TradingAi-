import { AppText, Screen } from '../../../components/ui';
import { PlannedFeature } from '../../../components/PlannedFeature';

export default function Portfolio() {
  return (
    <Screen title="Portfolio" subtitle="Positions, orders and P&L">
      <AppText tone="muted">Paper and live results are kept separate and always labelled.</AppText>
      <PlannedFeature id="portfolio" />
      <PlannedFeature id="positions" />
      <PlannedFeature id="orders" />
      <PlannedFeature id="reports" />
    </Screen>
  );
}
