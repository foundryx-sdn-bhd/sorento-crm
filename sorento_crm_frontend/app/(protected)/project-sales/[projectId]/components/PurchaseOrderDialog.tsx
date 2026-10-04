'use client';

import * as React from 'react';
import { useQueries } from '@tanstack/react-query';
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import {
  useProjectParties,
  usePurchaseOrderMutations,
  useQuotations,
  versionsKey,
} from '../../_shared/hooks/useProjects';
import { listQuotationVersions } from '../../_shared/services/projectService';
import type { PoSource, Project } from '../../_shared/types/project.types';

const SOURCES: { value: PoSource; label: string; description: string }[] = [
  {
    value: 'contractor_direct',
    label: 'Contractor direct',
    description: 'The main contractor bought from us themselves',
  },
  {
    value: 'trading_house',
    label: 'Trading house',
    description: 'Bought through a dealer or trading house',
  },
];

/**
 * Record a customer PO. CREATE only.
 *
 * Editing one is the PO's own page in edit mode (`pos/[poId]`), where the same fields sit in the
 * same order the reader already knows them in. This dialog used to do both, which meant a PO's
 * header had two editing surfaces with two layouts - the thing the view-and-edit-are-the-same
 * rule exists to stop.
 *
 * Every version is offered here, including superseded ones -- unlike the sample dialog.
 * The contractor buys off the document they were given, which is frequently not the
 * newest one, and the whole point of binding to a version is to compare against what
 * they were actually shown (AC-F9).
 */
export function PurchaseOrderDialog({
  project,
  onDone,
}: {
  project: Project;
  onDone: () => void;
}) {
  const { create } = usePurchaseOrderMutations(project.id);
  const quotations = useQuotations(project.id);
  const parties = useProjectParties({ limit: 200 });

  const scopes = React.useMemo(() => quotations.data ?? [], [quotations.data]);
  const versionQueries = useQueries({
    queries: scopes.map((scope) => ({
      queryKey: versionsKey(scope.id),
      queryFn: () => listQuotationVersions(scope.id),
    })),
  });

  const [poNumber, setPoNumber] = React.useState('');
  const [source, setSource] = React.useState<string>('contractor_direct');
  const [versionId, setVersionId] = React.useState('');
  const [issuerId, setIssuerId] = React.useState('');
  const [poDate, setPoDate] = React.useState('');
  const [amount, setAmount] = React.useState('');
  const [notes, setNotes] = React.useState('');

  const pending = create.isPending;

  const versionOptions = React.useMemo(
    () =>
      scopes.flatMap((scope, index) =>
        (versionQueries[index]?.data ?? []).map((version) => ({
          value: version.id,
          label: `${scope.scope_label} v${version.version_no}`,
          description: version.is_current ? 'Current' : 'Superseded',
        })),
      ),
    [scopes, versionQueries],
  );

  return (
    <Dialog open onOpenChange={(next) => !next && onDone()}>
      <DialogContent className="max-h-[92vh] w-full max-w-lg overflow-hidden">
        <DialogHeader>
          <DialogTitle>Record a purchase order</DialogTitle>
          <DialogDescription>
            The first PO on this project moves it to PO Received.
          </DialogDescription>
        </DialogHeader>

        <form
          onSubmit={async (event) => {
            event.preventDefault();
            await create.mutateAsync({
              po_number: poNumber.trim(),
              po_source: source as PoSource,
              quotation_version_id: versionId || null,
              issuing_party_id: issuerId || null,
              po_date: poDate || null,
              po_amount: amount.trim() || null,
              notes: notes.trim() || null,
            });
            onDone();
          }}
        >
          <DialogBody className="max-h-[65vh] space-y-4 overflow-y-auto">
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label htmlFor="po-number">
                  PO number <span className="text-destructive">*</span>
                </Label>
                <Input
                  id="po-number"
                  value={poNumber}
                  onChange={(event) => setPoNumber(event.target.value)}
                  placeholder="The number on their document"
                  required
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="po-date">PO date</Label>
                <Input
                  id="po-date"
                  type="date"
                  value={poDate}
                  onChange={(event) => setPoDate(event.target.value)}
                />
              </div>
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="po-source">Bought</Label>
              <SearchableSelect
                id="po-source"
                value={source}
                onChange={setSource}
                options={SOURCES}
                placeholder="Select"
              />
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="po-issuer">Issued by</Label>
              <SearchableSelect
                id="po-issuer"
                value={issuerId}
                onChange={setIssuerId}
                clearable
                loadError={parties.error}
                onRetry={() => void parties.refetch()}
                options={(parties.data?.data ?? []).map((party) => ({
                  value: party.id,
                  label: party.name,
                  description: party.party_type.replace(/_/g, ' '),
                }))}
                placeholder="-"
                emptyMessage="No parties on file"
              />
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="po-version">Quoted version it answers</Label>
              <SearchableSelect
                id="po-version"
                value={versionId}
                onChange={setVersionId}
                clearable
                options={versionOptions}
                placeholder="Not tied to a quotation"
                emptyMessage="Nothing is quoted on this project yet"
              />
              <p className="text-xs text-muted-foreground">
                Pick the version the contractor was actually holding, superseded or not.
                Leave it empty and the lines get no comparison at all.
              </p>
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="po-amount">PO amount (RM)</Label>
              <Input
                id="po-amount"
                type="number"
                step="0.01"
                min="0"
                value={amount}
                onChange={(event) => setAmount(event.target.value)}
                placeholder="Only needed when you are not entering lines"
              />
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="po-notes">Notes</Label>
              <Textarea
                id="po-notes"
                value={notes}
                onChange={(event) => setNotes(event.target.value)}
                rows={2}
                placeholder="Delivery instructions, staged ordering, anything unusual"
              />
            </div>
          </DialogBody>

          <DialogFooter className="flex-col gap-2 sm:flex-row sm:justify-end">
            <Button type="button" variant="outline" onClick={onDone}>
              Cancel
            </Button>
            <Button type="submit" disabled={!poNumber.trim() || pending}>
              Record PO
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
