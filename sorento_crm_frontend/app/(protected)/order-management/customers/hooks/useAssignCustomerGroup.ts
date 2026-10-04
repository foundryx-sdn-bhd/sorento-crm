import { useMutation, useQueryClient } from '@tanstack/react-query';
import { toast } from '@/lib/toast';
import {
  addCustomerGroupCustomers,
  createCustomerGroup,
} from '@/app/(protected)/order-management/customer-groups/services/customerGroupService';

export type AssignCustomerGroupInput = {
  customerIds: string[];
  /** An existing group... */
  group?: { id: string; name: string };
  /** ...or a name to create first. */
  newName?: string;
};

class GroupCreatedError extends Error {}

/** Put the picked customers into one group, creating the group first when given a new name. */
export function useAssignCustomerGroup() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ customerIds, group, newName }: AssignCustomerGroupInput) => {
      const target = group ?? (await createCustomerGroup({ name: newName ?? '' }));
      try {
        await addCustomerGroupCustomers(target.id, customerIds);
      } catch (error) {
        // The group exists by now; say so, or the user retries and hits a duplicate name.
        if (!group) {
          const message = error instanceof Error ? error.message : 'Failed to set customer group';
          throw new GroupCreatedError(`Group ${target.name} created; ${message}`);
        }
        throw error;
      }
      return { name: target.name, count: customerIds.length };
    },
    onSuccess: ({ name, count }) => {
      void queryClient.invalidateQueries({ queryKey: ['customers'] });
      void queryClient.invalidateQueries({ queryKey: ['customer-groups'] });
      toast.success(`${count} customer${count === 1 ? '' : 's'} set to group ${name}`);
    },
    onError: (error: Error) => {
      if (error instanceof GroupCreatedError) {
        void queryClient.invalidateQueries({ queryKey: ['customer-groups'] });
      }
      toast.error(error.message || 'Failed to set customer group');
    },
  });
}
