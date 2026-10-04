import { useMutation, useQueryClient } from '@tanstack/react-query';
import { toast } from '@/lib/toast';
import { assignSalesAgentCustomers } from '@/app/(protected)/master-data-management/sales-agents/services/salesAgentService';

/** Move the picked customers to one sales agent; the service already surfaces the API error. */
export function useAssignSalesAgentCustomers() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ agentId, customerIds }: { agentId: string; customerIds: string[] }) =>
      assignSalesAgentCustomers(agentId, customerIds),
    onSuccess: (_data, { customerIds }) => {
      void queryClient.invalidateQueries({ queryKey: ['customers'] });
      const n = customerIds.length;
      toast.success(`Sales agent set on ${n} customer${n === 1 ? '' : 's'}`);
    },
    onError: (error: Error) => {
      toast.error(error.message || 'Failed to set sales agent');
    },
  });
}
