import { Screen } from '../../components/ui';
import { ActiveModeHeader } from '../../features/account/ActiveModeBadge';
import { OrdersList } from '../../features/portfolio/Lists';

export default function Orders() {
  return (
    <Screen edges={['bottom']}>
      <ActiveModeHeader />
      <OrdersList />
    </Screen>
  );
}
