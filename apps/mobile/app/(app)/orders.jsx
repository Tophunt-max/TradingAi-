import { Screen } from '../../components/ui';
import { OrdersList } from '../../features/portfolio/Lists';

export default function Orders() {
  return (
    <Screen edges={['bottom']}>
      <OrdersList />
    </Screen>
  );
}
