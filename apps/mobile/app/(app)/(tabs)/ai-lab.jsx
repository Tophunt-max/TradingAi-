import { AppText, Screen } from '../../../components/ui';
import { PlannedFeature } from '../../../components/PlannedFeature';

export default function AiLab() {
  return (
    <Screen title="AI Lab" subtitle="Analysis, signals and model performance">
      <AppText tone="muted">
        The analysis engine is not built yet. When it is, every result will show its data freshness, probability distribution,
        calibration and the reasons to abstain. A high probability alone will never authorise a trade.
      </AppText>
      <PlannedFeature id="ai-analysis" />
      <PlannedFeature id="charts" />
      <PlannedFeature id="model-lab" />
      <PlannedFeature id="backtesting" />
    </Screen>
  );
}
