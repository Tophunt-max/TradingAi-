import { Screen } from '../../components/ui';
import { ActiveModeHeader } from '../../features/account/ActiveModeBadge';
import { PositionsList } from '../../features/portfolio/Lists';

export default function Positions() {
  return (
    <Screen edges={['bottom']}>
      <ActiveModeHeader />
      <PositionsList />
    </Screen>
  );
}
