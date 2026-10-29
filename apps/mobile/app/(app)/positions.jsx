import { Screen } from '../../components/ui';
import { PositionsList } from '../../features/portfolio/Lists';

export default function Positions() {
  return (
    <Screen edges={['bottom']}>
      <PositionsList />
    </Screen>
  );
}
