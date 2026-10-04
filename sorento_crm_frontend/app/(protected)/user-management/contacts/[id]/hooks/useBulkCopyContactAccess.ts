import { useMutation, useQueryClient } from '@tanstack/react-query';

import { bulkCopyContactAccess } from '../services/contactAccessCopyService';

/**
 * CONTACT-BULK-ACCESS: the copy dialog's two calls. `preview` is the dry run (writes
 * nothing, no cache to refresh); `apply` writes and refreshes the contacts list whether it
 * answered or failed, since some contacts may already be written.
 */
export function useBulkCopyContactAccess(targetContactIds: string[]) {
  const queryClient = useQueryClient();
  const preview = useMutation({
    mutationFn: (sourceContactId: string) =>
      bulkCopyContactAccess({ sourceContactId, targetContactIds, dryRun: true }),
  });
  const apply = useMutation({
    mutationFn: (sourceContactId: string) =>
      bulkCopyContactAccess({ sourceContactId, targetContactIds, dryRun: false }),
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: ['respond-contacts'] });
    },
  });
  return { preview, apply };
}
