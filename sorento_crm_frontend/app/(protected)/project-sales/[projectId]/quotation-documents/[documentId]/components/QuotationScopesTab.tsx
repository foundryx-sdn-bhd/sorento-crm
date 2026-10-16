'use client';

import * as React from 'react';
import Link from 'next/link';
import { SquarePen } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { useQuotations } from '../../../../_shared/hooks/useProjects';
import { OutcomePill } from '../../../../_shared/components/OutcomePill';
import { QuotationOutcomeDialog } from '../../../components/QuotationOutcomeDialog';
import { QuotationVersionEditor } from '../../../components/QuotationVersionEditor';
import { useQuotationDocumentScreen } from './QuotationDocumentContext';
import { isLockedForApproval } from '../../../../_shared/lib/quotationDecision';
import { QuotationScopeTabs } from './QuotationScopeTabs';

/**
 * The Lines tab (the owner renamed Scopes to Lines, #1341): the scope strip and the priced lines
 * under whichever scope is open.
 *
 * A READ (#1341). The owner: "editing of scope should be done by 'Edit Quotation', not a separate
 * button like this, Edit quotation means I edit the whole quotation". So there is no per-scope
 * Edit scope, no instant Add a scope and no "Press Edit" hint: a scope's name, series and lines
 * change in the form page opened from the gear. Record outcome, Recheck alerts and Revise stay,
 * because they are acts on the scope, not edits of it.
 *
 * The document itself is not fetched here - it comes from the layout, so the tabs cannot end up
 * with two answers about the same quotation.
 */
export function QuotationScopesTab() {
  const {
    projectId,
    documentId,
    document: record,
    project,
    canEdit,
    activeScopeId,
    selectScope,
  } = useQuotationDocumentScreen();
  const quotations = useQuotations(projectId);

  const [decidingScopeId, setDecidingScopeId] = React.useState<string | null>(null);

  const scopes = record.scopes ?? [];
  const activeScope = scopes.find((scope) => scope.id === activeScopeId) ?? scopes[0] ?? null;
  const activeQuotation =
    (quotations.data ?? []).find((row) => row.id === activeScope?.id) ?? null;

  if (scopes.length === 0) {
    // Rendered, never hidden: a quotation with no scopes is a real state. Its next step is the
    // form page, where scopes and their lines are added and saved together.
    return (
      <Card>
        <CardContent className="px-6 py-10 text-center">
          <h3 className="text-sm font-semibold">No lines on this quotation yet</h3>
          {canEdit && (
            <Button asChild variant="outline" size="sm" className="mt-4">
              <Link href={`/project-sales/${projectId}/quotation-documents/${documentId}/edit`}>
                <SquarePen className="size-4" aria-hidden />
                Edit quotation
              </Link>
            </Button>
          )}
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-5">
      <QuotationScopeTabs
        scopes={scopes}
        activeScopeId={activeScope?.id ?? ''}
        onSelect={selectScope}
        canEdit={canEdit}
      />

      {activeQuotation ? (
        <Card>
          {/* The scope's own commercial result, beside the lines it applies to. Outcome is per
              SCOPE and the project's outcome is derived from it. */}
          <CardHeader className="flex flex-col items-start gap-3 border-b border-border sm:flex-row sm:items-center sm:justify-between">
            <div className="flex min-w-0 flex-wrap items-center gap-2">
              <CardTitle className="min-w-0 break-words text-sm">
                {activeQuotation.scope_label}
              </CardTitle>
              <OutcomePill outcome={activeQuotation.outcome} />
              {/* Which series this scope is quoted from, stated rather than assumed: a scope
                  naming no series checks nothing, and its lines look identically clean. */}
              <span className="text-xs text-muted-foreground">
                {activeQuotation.series_name || 'No series'}
              </span>
              {activeQuotation.loss_reason_label && (
                <span className="text-xs text-muted-foreground">
                  {activeQuotation.loss_reason_label}
                </span>
              )}
            </div>
            {canEdit && (
              <Button
                type="button"
                size="sm"
                variant="outline"
                onClick={() => setDecidingScopeId(activeQuotation.id)}
              >
                Record outcome
              </Button>
            )}
          </CardHeader>
          {/* min-w-0 is load-bearing: CardContent is a flex item, and without it the line grid
              stretches the Card and the whole PAGE scrolls sideways at phone width. */}
          <CardContent className="min-w-0 py-5">
            {/* Keyed by scope so switching tabs gives the editor a clean instance rather than one
                still holding the previous scope's selected version. */}
            <QuotationVersionEditor
              key={activeQuotation.id}
              project={project}
              quotation={activeQuotation}
              // Pending or approved: the whole quotation is locked, and the way out is the
              // header's Edit quotation (Revise with a reason), not a per-scope revise.
              locked={isLockedForApproval(record)}
            />
          </CardContent>
        </Card>
      ) : quotations.isLoading ? (
        <Skeleton className="h-64 w-full" />
      ) : (
        <Card>
          <CardContent className="px-6 py-10 text-center">
            <h3 className="text-sm font-semibold">This scope could not be opened</h3>
            <p className="mx-auto mt-1 max-w-md text-sm text-muted-foreground">
              Its lines are not available right now. Reload the page to try again.
            </p>
          </CardContent>
        </Card>
      )}

      {decidingScopeId && activeQuotation && (
        <QuotationOutcomeDialog
          project={project}
          quotation={activeQuotation}
          onDone={() => setDecidingScopeId(null)}
        />
      )}
    </div>
  );
}
