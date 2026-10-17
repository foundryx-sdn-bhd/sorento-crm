'use client';

import * as React from 'react';
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { SquarePen, Trash2, Upload } from 'lucide-react';
import { numberFieldError } from '@/lib/number-field';
import { toast } from '@/lib/toast';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { DropdownMenuItem, DropdownMenuSeparator } from '@/components/ui/dropdown-menu';
import { Skeleton } from '@/components/ui/skeleton';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { ConfirmDeleteDialog } from '@/components/common/ConfirmDeleteDialog';
import { DetailActionsMenu } from '@/components/common/DetailActionsMenu';
import DetailActions from '@/components/common/DetailActions';
import RecordNavigation from '@/components/common/RecordNavigation';
import { formatDateInMalaysia, formatDateTimeInMalaysia } from '@/lib/helpers';
import {
  useProject,
  usePurchaseOrderMutations,
  usePurchaseOrders,
} from '../../../../_shared/hooks/useProjects';
import type {
  ProjectPurchaseOrder,
  ProjectPurchaseOrderSaveBody,
} from '../../../../_shared/types/project.types';
import { POIntakeUploadDialog } from '../../../components/POIntakeUploadDialog';
import { POIntakeVersionsStrip } from '../../../components/POIntakeVersionsStrip';
import { POToSalesOrderStep } from '../../../components/POToSalesOrderStep';
import { PurchaseOrderHeaderCard, PO_AMOUNT_RULES } from '../../../components/PurchaseOrderHeaderCard';
import {
  PurchaseOrderLinesEditor,
  stagedPoLinesToBody,
  stagedPoLinesTotal,
  unfinishedStagedPoLines,
} from '../../../components/PurchaseOrderLinesEditor';
import { SOURCE_LABELS, describeDrift } from '../../../components/PurchaseOrdersPanel';
import { formatMyr } from '../../../components/QuotationsPanel';
import { usePurchaseOrderEditSession } from './usePurchaseOrderEditSession';

/**
 * One customer PO, on its own page.
 *
 * The POs tab used to render the list AND the selected PO's documents, readiness step and
 * ninety-odd lines beneath it. The client's words: "seeing everything in 1 page is very
 * cramped". The list answers "what POs are on this project"; this answers "what is in this
 * one".
 *
 * It is an edit VIEW, the way the quotation document already is, and for the same complaint:
 * "every addition of line doesn't trigger a save, cause now i delete each line, then you ask me
 * to confirm, then when i add line, you also trigger save, very annoying". So by default nothing
 * on this page can be typed into. Edit turns the header fields into inputs IN PLACE and the line
 * table into a spreadsheet; one Save sends the header and the whole line set in one request; and
 * Cancel throws the lot away. Nothing is written until the button is pressed.
 */
export function PurchaseOrderDetailClient({
  projectId,
  poId,
}: {
  projectId: string;
  poId: string;
}) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const project = useProject(projectId);
  const purchaseOrders = usePurchaseOrders(projectId);
  const { update, remove } = usePurchaseOrderMutations(projectId);
  const edit = usePurchaseOrderEditSession();
  // The pager's set is the project's own POs, in the order the tab lists them.
  // The project's POs are already in memory (the record itself is read out of
  // them), so the pager is presentational rather than a second endpoint.
  const poIndex = (purchaseOrders.data ?? []).findIndex((row) => row.id === poId);

  const [uploading, setUploading] = React.useState(false);
  const [confirmDelete, setConfirmDelete] = React.useState(false);
  const [confirmRemovals, setConfirmRemovals] = React.useState(false);
  const [isSaving, setIsSaving] = React.useState(false);
  const [amountRefused, setAmountRefused] = React.useState(false);

  const po = (purchaseOrders.data ?? []).find((row) => row.id === poId) ?? null;
  const canEdit = Boolean(project.data?.can_edit);

  /**
   * `?edit=1` opens the session on arrival, so the list's Edit lands the user in the same one
   * screen rather than in a second form that collects the same fields. Fired once: re-running
   * it after Cancel would put the user straight back into the session they just left.
   */
  const wantsEdit = searchParams.get('edit') === '1';
  const opened = React.useRef(false);
  const begin = edit.begin;
  React.useEffect(() => {
    if (!wantsEdit || opened.current || !canEdit) return;
    opened.current = true;
    begin();
  }, [begin, canEdit, wantsEdit]);

  /**
   * Warn before the browser throws the staged work away. Only covers leaving the SITE (a
   * refresh, a closed tab, an external link), which is the same cover the quotation document has.
   */
  const isDirty = edit.isDirty;
  React.useEffect(() => {
    if (!isDirty) return;
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = '';
    };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [isDirty]);

  if (purchaseOrders.isLoading || project.isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-10 w-2/3" />
        <Skeleton className="h-48 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  if (!po || !project.data) {
    return (
      <div className="rounded-lg border border-destructive/40 bg-destructive/5 px-6 py-10 text-center">
        <h2 className="text-sm font-semibold text-destructive">
          This purchase order could not be loaded
        </h2>
        <p className="mx-auto mt-1 max-w-md text-sm text-muted-foreground">
          {purchaseOrders.error instanceof Error
            ? purchaseOrders.error.message
            : 'It may have been deleted.'}
        </p>
        <Button asChild variant="outline" className="mt-4">
          <Link href={`/project-sales/${projectId}?tab=pos`}>Back to POs</Link>
        </Button>
      </div>
    );
  }

  /**
   * The PO as the SCREEN currently stands: the server's row with whatever header edits are
   * staged merged over it.
   *
   * Merged here, once, rather than per field further down, because three places read these
   * values - the title, the facts line under it, and the header card - and a session that only
   * reached one of them would have the card naming one issuer while the heading two centimetres
   * above it named another.
   */
  const shown: ProjectPurchaseOrder = { ...po, ...edit.headerDraft };
  const isEditing = canEdit && edit.isEditing;
  const liveTotal = edit.staged ? stagedPoLinesTotal(edit.staged) : null;
  const drift = describeDrift(po);
  const publishedOrders = po.published_sales_order_count ?? 0;

  /**
   * Why the screen is in the state it is in, and what to do about it, in one sentence each.
   * The quotation header's own rule: a reason with no next move is what people read and still
   * cannot act on.
   */
  const headerHints: string[] = [];
  if (isEditing) {
    headerHints.push('Nothing is written until you press Save.');
    if (publishedOrders > 0) {
      headerHints.push(
        `${publishedOrders} sales order${publishedOrders === 1 ? '' : 's'} already went out from this PO. Changing it here does not change ${publishedOrders === 1 ? 'it' : 'them'}.`,
      );
    }
  } else if (!canEdit) {
    headerHints.push('You can read this purchase order but not change it.');
  }

  async function runSave() {
    setIsSaving(true);
    try {
      const body: ProjectPurchaseOrderSaveBody = { ...edit.headerDraft };
      // The lines are sent ONLY when they moved. The write replaces the whole set, so sending
      // it back untouched would be a real rewrite of rows nobody edited.
      if (edit.linesChanged) body.lines = stagedPoLinesToBody(edit.staged ?? []);
      await update.mutateAsync({ id: poId, body });
      edit.cancel();
    } catch {
      // The mutation already toasted the reason, and the session is deliberately left open:
      // what was typed is still on screen and still saveable.
    } finally {
      setIsSaving(false);
    }
  }

  function requestSave() {
    if (numberFieldError(shown.po_amount, PO_AMOUNT_RULES)) {
      setAmountRefused(true);
      return;
    }
    if (!(shown.po_number ?? '').trim()) {
      // The server refuses this with a 422; saying it here saves the round trip and points at
      // the field rather than at the whole record.
      toast.error('A PO needs its number - it is how the contractor refers to it.');
      return;
    }
    // A line the server would refuse is caught here rather than half-way through a save. The
    // cell is already marked; this says how many.
    const unfinished = unfinishedStagedPoLines(edit.staged ?? []);
    if (unfinished > 0) {
      toast.error(
        unfinished === 1
          ? 'One line still needs a product or the code on the PO.'
          : `${unfinished} lines still need a product or the code on the PO.`,
      );
      return;
    }
    // The one confirmation of the whole edit view. Staging a removal destroyed nothing, so it
    // asked nothing; this is the moment lines actually leave the PO.
    if (edit.removedCount > 0) {
      setConfirmRemovals(true);
      return;
    }
    void runSave();
  }

  return (
    <div className="space-y-5">
      <header className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0 break-words">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm text-muted-foreground">{project.data.project_code}</span>
            <Badge variant="secondary" appearance="light">
              {SOURCE_LABELS[shown.po_source] ?? shown.po_source}
            </Badge>
          </div>
          <h2 className="mt-1 text-xl font-semibold break-words">{shown.po_number}</h2>
          <p className="text-sm text-muted-foreground break-words">
            {[
              formatMyr(liveTotal ?? po.line_total),
              shown.issuing_party_name,
              shown.po_date ? formatDateInMalaysia(shown.po_date) : null,
              drift || null,
            ]
              .filter(Boolean)
              .join(' · ')}
          </p>
          {/* Read-only metadata belongs in the header, never inside a section that has an edit
              counterpart: it has none, and a field that appears in the read and vanishes in the
              edit is what makes the two views disagree. */}
          {po.updated_at && (
            <p className="text-xs text-muted-foreground">
              {`Last updated ${formatDateTimeInMalaysia(po.updated_at)}`}
            </p>
          )}
        </div>

        <div className="flex flex-col items-stretch gap-1.5 sm:items-end">
          {/* Pager, gear, primary (D6), through the shared group rather than a hand-rolled
              row: the order is the same rule on all 39 detail pages, and a copy of it here
              is a copy that can drift. */}
          <DetailActions
            pagerNode={
              /* Walking a project's POs one after another is the normal case, so the pager
                comes first, the way it does on the user record.

                Hidden while a session is open, exactly as the quotation document hides it. The
                staged work would in fact survive a step away - it lives in the session, not in
                the table - but a pager sitting beside Cancel and Save READS like it will
                discard it, and a control nobody dares press is worse than one that is absent. */
              !isEditing && (
                <RecordNavigation
                  index={poIndex >= 0 ? poIndex + 1 : null}
                  total={(purchaseOrders.data ?? []).length}
                  hasPrevious={poIndex > 0}
                  hasNext={poIndex >= 0 && poIndex < (purchaseOrders.data ?? []).length - 1}
                  onPrevious={() =>
                    router.push(
                      `/project-sales/${projectId}/pos/${(purchaseOrders.data ?? [])[poIndex - 1].id}`,
                    )
                  }
                  onNext={() =>
                    router.push(
                      `/project-sales/${projectId}/pos/${(purchaseOrders.data ?? [])[poIndex + 1].id}`,
                    )
                  }
                  isLoading={purchaseOrders.isLoading}
                  ariaLabel="purchase order"
                />
              )
            }
            gear={
              /* Everything that is not the one call to action lives behind the gear. Only
                rendered for somebody who may act: a reader has nothing to put in it. */
              canEdit && (
                <DetailActionsMenu ariaLabel="Purchase order actions">
                  <DropdownMenuItem
                    disabled={isEditing}
                    onSelect={() => setUploading(true)}
                  >
                    <Upload className="size-4" aria-hidden />
                    Upload a document
                  </DropdownMenuItem>
                  {/* Destructive last, and behind a separator. */}
                  <DropdownMenuSeparator />
                  <DropdownMenuItem
                    variant="destructive"
                    disabled={isEditing}
                    onSelect={() => setConfirmDelete(true)}
                  >
                    <Trash2 className="size-4" aria-hidden />
                    Delete this PO
                  </DropdownMenuItem>
                </DetailActionsMenu>
              )
            }
            primary={
              /* The one thing this record is for: correcting what was read off the document.
                Once a session is open, Cancel and Save ARE the header's controls and the
                entry point has nothing left to say. */
              canEdit && isEditing ? (
                <>
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    disabled={isSaving}
                    onClick={() => edit.cancel()}
                  >
                    Cancel
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    disabled={isSaving || !edit.isDirty}
                    title={edit.isDirty ? undefined : 'Nothing has changed yet'}
                    onClick={requestSave}
                  >
                    {isSaving ? 'Saving...' : 'Save purchase order'}
                  </Button>
                </>
              ) : canEdit ? (
                <Button type="button" size="sm" onClick={() => edit.begin()}>
                  <SquarePen className="size-4" aria-hidden />
                  Edit the PO
                </Button>
              ) : null
            }
          />
          {/* Visible, not only a tooltip: a reason hidden behind a hover is unreadable on the
              phone this page also has to work on. */}
          {headerHints.map((hint) => (
            <p key={hint} className="text-xs text-muted-foreground sm:text-right">
              {hint}
            </p>
          ))}
        </div>
      </header>

      {canEdit && (
        <POToSalesOrderStep
          projectId={projectId}
          purchaseOrder={po}
          readiness={{
            poConfirmed: Boolean(po.po_confirmed),
            scheduleConfirmed: Boolean(po.schedule_confirmed),
          }}
        />
      )}

      <PurchaseOrderHeaderCard
        projectId={projectId}
        po={shown}
        liveTotal={liveTotal}
        showAmountError={amountRefused}
        onChange={isEditing ? edit.stageHeader : undefined}
      />

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Documents</CardTitle>
        </CardHeader>
        <CardContent>
          <POIntakeVersionsStrip
            projectId={projectId}
            poId={po.id}
            canEdit={canEdit}
            onUpload={() => setUploading(true)}
          />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Lines</CardTitle>
        </CardHeader>
        <CardContent>
          <PurchaseOrderLinesEditor
            project={project.data}
            // The STAGED row, so clearing the bound version in the header immediately says
            // "no price is checked" over the lines it stops checking.
            po={shown}
            edit={
              isEditing
                ? {
                    staged: edit.staged,
                    seed: edit.seed,
                    stage: edit.stage,
                    toggleRemoved: edit.toggleRemoved,
                  }
                : null
            }
          />
        </CardContent>
      </Card>

      {uploading && (
        <POIntakeUploadDialog
          projectId={projectId}
          purchaseOrderId={po.id}
          purchaseOrderNumber={po.po_number}
          onDone={() => setUploading(false)}
        />
      )}

      {/* The edit view's ONE destructive confirmation. Staging a removal destroys nothing and so
          asks nothing; this is the moment the lines actually leave, and it names how many. */}
      <AlertDialog open={confirmRemovals} onOpenChange={setConfirmRemovals}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Confirm delete</AlertDialogTitle>
            <AlertDialogDescription>
              {`Saving removes ${edit.removedCount} ${
                edit.removedCount === 1 ? 'line' : 'lines'
              } from ${shown.po_number}. This action cannot be undone.`}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={isSaving}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              disabled={isSaving}
              onClick={(event) => {
                // Held open by hand: the dialog closes itself on the click, and the save that
                // follows would then have no place to report a failure back to.
                event.preventDefault();
                void runSave().then(() => setConfirmRemovals(false));
              }}
            >
              {`Save and remove ${edit.removedCount} ${
                edit.removedCount === 1 ? 'line' : 'lines'
              }`}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <ConfirmDeleteDialog
        open={confirmDelete}
        onOpenChange={setConfirmDelete}
        title="Confirm delete"
        description={`Delete ${po.po_number} and its ${po.line_count} line${po.line_count === 1 ? '' : 's'}? This action cannot be undone. The project stays at PO Received, because it genuinely passed through it.`}
        onDelete={async () => {
          await remove.mutateAsync(po.id);
        }}
        onSuccess={() => router.push(`/project-sales/${projectId}?tab=pos`)}
        successMessage="Purchase order deleted"
      />
    </div>
  );
}
