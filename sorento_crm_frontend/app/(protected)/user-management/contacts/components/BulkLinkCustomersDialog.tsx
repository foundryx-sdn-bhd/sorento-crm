'use client';

import { useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { SearchableMultiSelect } from '@/components/common/SearchableMultiSelect';
import { toast } from '@/lib/toast';
import { useCustomerMultiPicker } from '@/app/(protected)/order-management/customers/hooks/useCustomerMultiPicker';
import { linkContactCustomers } from '../[id]/services/contactCustomersService';
import type { RespondContact } from '../types/contact.types';

type Props = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  contacts: RespondContact[];
  /** Called with the ids that linked fine, so the list can deselect exactly those. */
  onLinked: (okContactIds: string[]) => void;
};

const contactLabel = (c: RespondContact) => c.name || c.phone_number;

export default function BulkLinkCustomersDialog({ open, onOpenChange, contacts, onLinked }: Props) {
  const queryClient = useQueryClient();
  const picker = useCustomerMultiPicker(() => false);
  const [busy, setBusy] = useState(false);

  const handleOpenChange = (next: boolean) => {
    if (!next) picker.clear();
    onOpenChange(next);
  };

  const apply = async () => {
    const customerIds = picker.selected;
    setBusy(true);
    const results = await Promise.allSettled(
      contacts.map((c) => linkContactCustomers(c.id, customerIds)),
    );
    setBusy(false);

    const okIds: string[] = [];
    const failures: string[] = [];
    results.forEach((r, i) => {
      if (r.status === 'fulfilled') okIds.push(contacts[i].id);
      else {
        const reason = r.reason instanceof Error ? r.reason.message : 'Failed to link customers';
        failures.push(`${contactLabel(contacts[i])}: ${reason}`);
      }
    });

    void queryClient.invalidateQueries({ queryKey: ['respond-contacts'] });
    void queryClient.invalidateQueries({ queryKey: ['contact-customers'] });
    void queryClient.invalidateQueries({ queryKey: ['customer-linked-contacts'] });

    if (failures.length === 0) {
      const n = customerIds.length;
      const m = contacts.length;
      toast.success(
        `Linked ${n} customer${n === 1 ? '' : 's'} to ${m} contact${m === 1 ? '' : 's'}`,
      );
    } else {
      toast.error(`Could not link: ${failures.join('; ')}`);
    }
    onLinked(okIds);
    handleOpenChange(false);
  };

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Link customers</DialogTitle>
        </DialogHeader>
        <DialogBody>
          <SearchableMultiSelect
            value={picker.selected}
            onChange={picker.setSelected}
            fetchOptions={picker.fetchOptions}
            selectedOptions={picker.selectedOptions}
            placeholder="Select customers"
            emptyMessage="No customers match."
            disabled={busy}
            className="w-full"
          />
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" onClick={() => handleOpenChange(false)}>
            Cancel
          </Button>
          <Button disabled={picker.selected.length === 0 || busy} onClick={() => void apply()}>
            Apply
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
