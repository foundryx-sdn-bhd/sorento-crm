'use client';

import { useState } from 'react';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { useAssignSalesAgentCustomers } from '../hooks/useAssignSalesAgentCustomers';
import { useCustomerSalesAgentOptions } from '../hooks/useCustomerSalesAgentOptions';

type Props = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  customerIds: string[];
  onDone: () => void;
};

export function BulkSetSalesAgentDialog({ open, onOpenChange, customerIds, onDone }: Props) {
  const [agentId, setAgentId] = useState('');
  const { options } = useCustomerSalesAgentOptions();
  const assign = useAssignSalesAgentCustomers();

  const handleOpenChange = (next: boolean) => {
    if (!next) setAgentId('');
    onOpenChange(next);
  };

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Set sales agent</DialogTitle>
        </DialogHeader>
        <DialogBody>
          <SearchableSelect
            value={agentId}
            onChange={setAgentId}
            options={options}
            placeholder="Select sales agent"
            emptyMessage="No sales agents match."
            clearable
          />
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" onClick={() => handleOpenChange(false)}>
            Cancel
          </Button>
          <Button
            disabled={!agentId || assign.isPending}
            onClick={() =>
              assign.mutate(
                { agentId, customerIds },
                {
                  // Closes either way: the toast carries the error, the selection stays.
                  onSuccess: onDone,
                  onSettled: () => handleOpenChange(false),
                },
              )
            }
          >
            Apply
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
