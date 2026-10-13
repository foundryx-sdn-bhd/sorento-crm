'use client';

import * as React from 'react';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Label } from '@/components/ui/label';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { useCustomerSelectQuery } from '@/app/(protected)/order-management/shared/hooks/use-customer-select-query';
import {
  InformantFieldset,
  informantBody,
  type InformantDraft,
} from '../../components/InformantFieldset';
import type {
  LeadInformantBody,
  LeadWithAcceptance,
} from '../../../_shared/types/leadAcceptance.types';
import type { ProjectLeadBody } from '../../../_shared/types/project.types';

/**
 * The two "who" questions on a lead, in one place and never in the same box.
 *
 * The informant told us about the job. The buyer will issue the purchase order, and is
 * usually unknown for weeks after the lead is worth recording.
 *
 * There is deliberately NO owner field here. `PUT /leads/{id}` still accepts
 * `owner_user_id` (phase-1 behaviour), and editing it would move a lead to a new person
 * without a clock, a notification or an acceptance. Changing who holds a lead goes
 * through `/assign` only.
 */
export function EditLeadInformantDialog({
  lead,
  submitting,
  onDone,
  onConfirm,
}: {
  lead: LeadWithAcceptance;
  submitting?: boolean;
  onDone: () => void;
  onConfirm: (body: Partial<ProjectLeadBody> & LeadInformantBody) => Promise<void>;
}) {
  const customers = useCustomerSelectQuery();
  const [informant, setInformant] = React.useState<InformantDraft>({
    informant_source: lead.informant_source ?? '',
    informant_ref: lead.informant_ref ?? '',
    informant_party_id: lead.informant_party_id ?? '',
    informant_contact_name: lead.informant_contact_name ?? '',
  });
  const [customerId, setCustomerId] = React.useState(lead.customer_id ?? '');
  const [busy, setBusy] = React.useState(false);
  const pending = busy || Boolean(submitting);

  return (
    <Dialog open onOpenChange={(next) => !next && onDone()}>
      <DialogContent className="max-h-[92vh] w-full max-w-lg overflow-hidden">
        <DialogHeader>
          <DialogTitle>Who told us, and who buys</DialogTitle>
          <DialogDescription>{lead.title}</DialogDescription>
        </DialogHeader>

        <form
          onSubmit={async (event) => {
            event.preventDefault();
            setBusy(true);
            try {
              await onConfirm({
                ...informantBody(informant),
                customer_id: customerId || null,
              });
              onDone();
            } finally {
              setBusy(false);
            }
          }}
        >
          <DialogBody className="max-h-[60vh] space-y-4 overflow-y-auto">
            <InformantFieldset
              value={informant}
              onChange={setInformant}
              idPrefix="edit-informant"
            />

            <div className="space-y-1.5">
              <Label htmlFor="edit-lead-buyer">Buyer</Label>
              <SearchableSelect
                id="edit-lead-buyer"
                value={customerId}
                onChange={setCustomerId}
                clearable
                options={(customers.data ?? []).map((row) => ({
                  value: row.id,
                  label: row.customer_name,
                  description: row.customer_code ?? undefined,
                }))}
                placeholder="Not known yet"
                emptyMessage="No match"
              />
            </div>
          </DialogBody>

          <DialogFooter className="flex-col gap-2 sm:flex-row sm:justify-end">
            <Button type="button" variant="outline" onClick={onDone}>
              Cancel
            </Button>
            <Button type="submit" disabled={pending}>
              Save informant
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
