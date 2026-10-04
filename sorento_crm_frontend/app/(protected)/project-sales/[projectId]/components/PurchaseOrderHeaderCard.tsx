'use client';

import * as React from 'react';
import { useQueries } from '@tanstack/react-query';
import { Card, CardContent } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { formatDateInMalaysia } from '@/lib/helpers';
import { useProjectParties, useQuotations, versionsKey } from '../../_shared/hooks/useProjects';
import { listQuotationVersions } from '../../_shared/services/projectService';
import { formatMyrExact } from '../../_shared/lib/money';
import type {
  PoSource,
  ProjectPurchaseOrder,
  ProjectPurchaseOrderBody,
} from '../../_shared/types/project.types';
import { SOURCE_LABELS } from './PurchaseOrdersPanel';

/**
 * What the PO says about itself: the number the contractor refers to it by, when it was
 * issued, who issued it, and which quoted version it answers.
 *
 * Every one of these fields lived ONLY in a modal before, so the PO's own page could not show
 * them and the way to read the bound version was to open the form that changes it. They are
 * facts about the record, so the record shows them - and in an edit session the same block
 * becomes inputs onto the same values, in the same order, so nothing moves between the two
 * views (ADR: view and edit are the same layout).
 *
 * The total is read-only in both, because it is the sum of the lines below rather than a field:
 * `liveTotal` is what the lines currently on screen come to, so the figure moves with them
 * instead of stating the last save's answer.
 */
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

function Field({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex gap-2 text-sm">
      <span className="w-28 shrink-0 text-muted-foreground">{label}</span>
      <span className="min-w-0 break-words font-medium">{value}</span>
    </div>
  );
}

/**
 * The same field with a way in. Stacked label over control rather than beside it, because the
 * side-by-side read layout leaves an input about eighty pixels wide on a phone.
 */
function EditField({
  id,
  label,
  children,
  hint,
}: {
  id: string;
  label: string;
  children: React.ReactNode;
  hint?: string;
}) {
  return (
    <div className="min-w-0 space-y-1.5">
      <Label htmlFor={id}>{label}</Label>
      {children}
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}

export function PurchaseOrderHeaderCard({
  projectId,
  po,
  liveTotal = null,
  onChange,
}: {
  projectId: string;
  /**
   * The PO as the SCREEN currently stands: the page hands down the server's row with whatever
   * header edits are staged merged over it, so one prop is both what to display and what an
   * input is currently holding.
   */
  po: ProjectPurchaseOrder;
  /** What the lines on screen come to, including edits nobody has saved yet. */
  liveTotal?: string | null;
  /**
   * Set only in an edit session. Absent means this is a read. Typing here writes nothing: it
   * stages onto the header draft, and the screen's one Save sends it with the lines.
   */
  onChange?: (patch: Partial<ProjectPurchaseOrderBody>) => void;
}) {
  const parties = useProjectParties({ limit: 200 });
  const quotations = useQuotations(projectId);
  const scopes = React.useMemo(() => quotations.data ?? [], [quotations.data]);
  /**
   * Every version of every scope, superseded ones included (AC-F9). The contractor buys off the
   * document they were given, which is frequently not the newest one, and the whole point of
   * binding to a version is to compare against what they were actually shown.
   *
   * Only fetched while editing: reading the PO needs the bound version's LABEL, which the row
   * already carries, and a read of every version of every scope would be a request per scope
   * for an answer nobody looks at.
   */
  const versionQueries = useQueries({
    queries: scopes.map((scope) => ({
      queryKey: versionsKey(scope.id),
      queryFn: () => listQuotationVersions(scope.id),
      enabled: Boolean(onChange),
    })),
  });

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

  const boundLabel = po.scope_label
    ? `${po.scope_label}${po.version_no ? ` v${po.version_no}` : ''}`
    : null;

  return (
    <Card>
      <CardContent className="grid gap-6 py-5 md:grid-cols-2">
        <div className="min-w-0 space-y-2">
          <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            The document
          </p>
          {onChange ? (
            <div className="space-y-3 pt-1">
              <EditField id="po-number" label="PO number">
                <Input
                  id="po-number"
                  value={po.po_number ?? ''}
                  onChange={(event) => onChange({ po_number: event.target.value })}
                  placeholder="The number on their document"
                  required
                />
              </EditField>
              <EditField id="po-date" label="PO date">
                <Input
                  id="po-date"
                  type="date"
                  value={(po.po_date ?? '').slice(0, 10)}
                  onChange={(event) => onChange({ po_date: event.target.value || null })}
                />
              </EditField>
              <EditField id="po-source" label="Bought">
                <SearchableSelect
                  id="po-source"
                  value={po.po_source}
                  onChange={(value) => onChange({ po_source: value as PoSource })}
                  options={SOURCES}
                  placeholder="Select"
                />
              </EditField>
              <EditField id="po-issuer" label="Issued by">
                <SearchableSelect
                  id="po-issuer"
                  value={po.issuing_party_id ?? ''}
                  onChange={(value) => onChange({ issuing_party_id: value || null })}
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
              </EditField>
            </div>
          ) : (
            <>
              <Field label="PO number" value={po.po_number} />
              <Field
                label="PO date"
                value={po.po_date ? formatDateInMalaysia(po.po_date) : '-'}
              />
              <Field
                label="Bought"
                value={SOURCE_LABELS[po.po_source] ?? po.po_source}
              />
              <Field label="Issued by" value={po.issuing_party_name ?? '-'} />
            </>
          )}
        </div>

        <div className="min-w-0 space-y-2">
          <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            What it is checked against
          </p>
          {onChange ? (
            <div className="space-y-3 pt-1">
              <EditField
                id="po-version"
                label="Quoted version it answers"
                hint="Pick the version the contractor was actually holding, superseded or not. Leave it empty and the lines get no comparison at all."
              >
                <SearchableSelect
                  id="po-version"
                  value={po.quotation_version_id ?? ''}
                  onChange={(value) => onChange({ quotation_version_id: value || null })}
                  clearable
                  options={versionOptions}
                  placeholder="Not tied to a quotation"
                  emptyMessage="Nothing is quoted on this project yet"
                />
              </EditField>
              <EditField
                id="po-amount"
                label="PO amount (RM)"
                hint="Only needed when you are not entering lines."
              >
                <Input
                  id="po-amount"
                  type="number"
                  step="0.01"
                  min="0"
                  value={po.po_amount ?? ''}
                  onChange={(event) => onChange({ po_amount: event.target.value || null })}
                  placeholder="0.00"
                />
              </EditField>
            </div>
          ) : (
            <>
              <Field label="Quoted version" value={boundLabel ?? 'Not tied to a quotation'} />
              <Field
                label="PO amount"
                value={po.po_amount ? formatMyrExact(po.po_amount) : '-'}
              />
            </>
          )}
          {/* Read in both views, because it is the sum of the lines rather than a field
              anybody types. The LIVE figure wins whenever there is one: what the reader is
              owed is the total of what is on the screen, not of what was last saved. */}
          <div className="flex gap-2 border-t border-border pt-2 text-sm">
            <span className="w-28 shrink-0 text-muted-foreground">Lines total</span>
            <span className="font-semibold tabular-nums">
              {formatMyrExact(liveTotal ?? po.line_total)}
            </span>
          </div>
        </div>

        <div className="border-t border-border pt-4 md:col-span-2">
          {onChange ? (
            <EditField id="po-notes" label="Notes">
              <Textarea
                id="po-notes"
                rows={2}
                value={po.notes ?? ''}
                onChange={(event) => onChange({ notes: event.target.value || null })}
                placeholder="Delivery instructions, staged ordering, anything unusual"
              />
            </EditField>
          ) : (
            <div className="space-y-1">
              <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                Notes
              </p>
              <p className="min-w-0 break-words text-sm">{po.notes ?? '-'}</p>
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
