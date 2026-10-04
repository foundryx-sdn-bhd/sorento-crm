'use client';

import { useMemo, useState } from 'react';
import { Unlink } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardHeading, CardTitle } from '@/components/ui/card';
import { Checkbox } from '@/components/ui/checkbox';
import { Skeleton } from '@/components/ui/skeleton';
import { SearchableMultiSelect } from '@/components/common/SearchableMultiSelect';
import { useDeferredBulkAction } from '@/hooks/useDeferredBulkAction';
import { useDeferredRowAction } from '@/hooks/useDeferredRowAction';
import { useHasPermission } from '@/hooks/usePermissions';
import { useCustomerMultiPicker } from '@/app/(protected)/order-management/customers/hooks/useCustomerMultiPicker';
import {
  contactCustomersKey,
  useContactCustomers,
  useLinkContactCustomers,
} from '../hooks/useContactCustomers';

function agentLabel(code: string | null, name: string | null): string | null {
  if (!code) return null;
  return name ? `${code} - ${name}` : code;
}

/**
 * Contact Details -> Customers card: the customer accounts this WhatsApp number belongs to,
 * each with the sales agent handling that account (derived off the customer, never typed here).
 * Read-only without `user_management.contacts.edit`.
 */
export default function ContactCustomersSection({ contactId }: { contactId: string }) {
  const canEdit = useHasPermission('user_management.contacts.edit');
  const { data, isLoading } = useContactCustomers(contactId);
  const link = useLinkContactCustomers(contactId);

  // Unlink asks nothing (D7): the button becomes the countdown, the server commits on lapse.
  const unlink = useDeferredRowAction({
    actionKey: 'contact_customer_link.unlink',
    entityType: 'contact_customer_link',
    verb: 'Unlinking',
    successMessage: 'Customer unlinked',
    surface: 'inline',
    invalidateKeys: [contactCustomersKey(contactId), ['customer-linked-contacts']],
  });

  // Ticked link ids. Cancel leaves them ticked; they clear once the unlink has settled.
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const bulkUnlink = useDeferredBulkAction({
    actionKey: 'contact_customer_link.unlink',
    entityType: 'contact_customer_link',
    verb: 'Unlinking',
    pastVerb: 'unlinked',
    surface: 'inline',
    describe: (count) => `${count} customer${count === 1 ? '' : 's'}`,
    invalidateKeys: [contactCustomersKey(contactId), ['customer-linked-contacts']],
    onFinished: () => setSelected(new Set()),
  });

  const links = useMemo(() => data?.data ?? [], [data]);
  const linkedIds = useMemo(() => new Set(links.map((l) => l.customer_id)), [links]);
  const picker = useCustomerMultiPicker((option) => linkedIds.has(option.value), undefined, {
    allGrantedCompanies: true,
  });

  const ticked = links.filter((l) => selected.has(l.id));
  const allTicked = links.length > 0 && ticked.length === links.length;
  const toggle = (id: string, on: boolean) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (on) next.add(id);
      else next.delete(id);
      return next;
    });

  return (
    <Card>
      <CardHeader className="flex-wrap gap-2">
        <CardHeading>
          <CardTitle>Customers</CardTitle>
        </CardHeading>
        {canEdit && links.length > 0 ? (
          <div className="flex flex-wrap items-center gap-2">
            <Checkbox
              aria-label="Select all customers"
              checked={allTicked}
              onCheckedChange={(v) =>
                setSelected(v === true ? new Set(links.map((l) => l.id)) : new Set())
              }
            />
            {bulkUnlink.countdown ??
              (ticked.length >= 1 ? (
                <Button
                  variant="outline"
                  size="sm"
                  disabled={bulkUnlink.isStarting}
                  onClick={() => bulkUnlink.run(ticked.map((l) => ({ id: l.id })))}
                >
                  <Unlink className="size-4" />
                  {`Unlink (${ticked.length})`}
                </Button>
              ) : null)}
          </div>
        ) : null}
      </CardHeader>
      <CardContent className="space-y-4">
        {canEdit ? (
          <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
            <SearchableMultiSelect
              value={picker.selected}
              onChange={picker.setSelected}
              fetchOptions={picker.fetchOptions}
              selectedOptions={picker.selectedOptions}
              placeholder="Add customers"
              emptyMessage="No customers match."
              disabled={link.isPending}
              className="w-full"
            />
            <Button
              type="button"
              aria-label="Link customers"
              disabled={picker.selected.length === 0 || link.isPending}
              onClick={() =>
                link.mutate(picker.selected, { onSuccess: () => picker.clear() })
              }
            >
              {picker.selected.length === 1
                ? 'Link 1 customer'
                : `Link ${picker.selected.length} customers`}
            </Button>
          </div>
        ) : null}

        {isLoading ? (
          <div className="space-y-2">
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
        ) : links.length === 0 ? (
          <div className="py-4 text-center">
            <p className="text-sm font-medium text-foreground">No customers linked</p>
            <p className="mt-1 text-sm text-muted-foreground">
              Link the customer accounts this contact belongs to
            </p>
          </div>
        ) : (
          <ul className="divide-y rounded-md border">
            {links.map((row) => {
              const agent = agentLabel(row.sales_agent_code, row.sales_agent_name);
              const code = `${row.customer_code} - ${row.customer_name}`;
              const customer = row.company_name ? `${row.company_name} · ${code}` : code;
              const counting = unlink.targetId === row.id && unlink.countdown;
              return (
                <li
                  key={row.id}
                  className="flex flex-col gap-2 px-3 py-2 sm:flex-row sm:items-center sm:justify-between"
                >
                  <div className="flex min-w-0 items-center gap-2">
                    {canEdit ? (
                      <Checkbox
                        aria-label={`Select ${customer}`}
                        checked={selected.has(row.id)}
                        onCheckedChange={(v) => toggle(row.id, v === true)}
                      />
                    ) : null}
                    <span className="truncate text-sm font-medium" title={customer}>
                      {customer}
                    </span>
                    {!row.is_active ? (
                      <Badge variant="secondary" appearance="light" size="md">
                        Inactive
                      </Badge>
                    ) : null}
                  </div>
                  <div className="flex flex-wrap items-center gap-x-3 gap-y-1 sm:justify-end">
                    <span
                      className="min-w-0 truncate text-sm text-muted-foreground"
                      title={agent ?? 'No sales agent'}
                    >
                      {agent ?? 'No sales agent'}
                    </span>
                    {canEdit ? (
                      <>
                        {counting ? (
                          counting
                        ) : (
                          <Button
                            variant="ghost"
                            size="sm"
                            disabled={unlink.isPending || (!!bulkUnlink.countdown && selected.has(row.id))}
                            onClick={() =>
                              unlink.run({ id: row.id, subject: row.customer_name })
                            }
                          >
                            <Unlink className="size-4" />
                            Unlink
                          </Button>
                        )}
                      </>
                    ) : null}
                  </div>
                </li>
              );
            })}
          </ul>
        )}

      </CardContent>
    </Card>
  );
}
