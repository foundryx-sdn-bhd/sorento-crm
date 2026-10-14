import { useQuery } from '@tanstack/react-query';
import { getSalesOrderDeliveries } from '../services/salesOrderService';

/** Deliveries under each line of one SO. `enabled` flips on at the first lightbox and stays on,
 *  so the read happens once per SO. */
export function useSalesOrderDeliveries(id: string | null, enabled: boolean) {
  return useQuery({
    queryKey: ['scm', 'sales-orders', 'deliveries', id],
    queryFn: () => getSalesOrderDeliveries(id as string),
    enabled: !!id && enabled,
    staleTime: 60_000,
    retry: 1,
  });
}
