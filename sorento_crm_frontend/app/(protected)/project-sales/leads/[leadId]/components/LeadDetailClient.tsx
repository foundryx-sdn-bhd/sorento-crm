'use client';

import * as React from 'react';
import Link from 'next/link';
import type { ColumnDef } from '@tanstack/react-table';
import { useRouter, useSearchParams } from 'next/navigation';
import { useSession } from 'next-auth/react';
import {
  ArrowRightLeft,
  Building2,
  Check,
  FolderKanban,
  History,
  Info,
  Loader2,
  Pencil,
  RotateCcw,
  Trash2,
  UserPlus,
  UserRound,
  Users,
} from 'lucide-react';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Badge } from '@/components/ui/badge';
import { useBackToListHref } from '@/components/common/BackToList';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { DropdownMenuItem } from '@/components/ui/dropdown-menu';
import { Skeleton } from '@/components/ui/skeleton';
import { ConfirmDeleteDialog } from '@/components/common/ConfirmDeleteDialog';
import { DetailActionsMenu } from '@/components/common/DetailActionsMenu';
import DetailActions from '@/components/common/DetailActions';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { PanelDataGrid } from '@/components/common/PanelDataGrid';
import { ProjectStatusPill } from '../../../[projectId]/components/ProjectStatusPill';
import { formatDateTimeInMalaysia } from '@/lib/helpers';
import { useProjectStatusGraph } from '@/app/(protected)/project-sales/_shared/hooks/useProjectStatusGraph';
import {
  leadsPagerQuery,
  useCustomerPortfolio,
  useLead,
  useLeadMutations,
} from '../../../_shared/hooks/useProjects';
import { useLeadAcceptanceMutations } from '../../../_shared/hooks/useLeadAcceptance';
import type { LeadWithAcceptance } from '../../../_shared/types/leadAcceptance.types';
import type { Project, ProjectLead } from '../../../_shared/types/project.types';
import { AssignLeadDialog } from '../../components/AssignLeadDialog';
import { DeclineLeadDialog } from '../../components/DeclineLeadDialog';
import { LeadAcceptanceBadge } from '../../components/LeadAcceptanceBadge';
import { LeadStatusPill } from '../../components/LeadStatusPill';
import { availableStatusMoves } from '../../../[projectId]/components/ProjectStatusAction';
import { canAssignLead, informantSourceLabel } from '../../components/acceptance';
import { DisqualifyLeadDialog } from './DisqualifyLeadDialog';
import { EditLeadInformantDialog } from './EditLeadInformantDialog';
import { LeadTimelinePanel } from './LeadTimelinePanel';
import { QualifyLeadDialog } from './QualifyLeadDialog';

/**
 * One recorded sighting: who told us, what we heard, and what it became.
 *
 * URL-routed tabs, the same shape the project detail page uses, because a lead and a
 * project are read by the same people minutes apart and a detail surface that scrolls
 * where its sibling tabs is read as a different product. One concern per tab, and every
 * tab renders even when it holds nothing, with the next step spelled out: a section that
 * vanishes when empty makes the feature look absent rather than unused.
 *
 * The two terminal rungs are NOT in the status dropdown. Qualified and Disqualified are
 * reached through their own buttons because each does work the rung alone cannot: one
 * runs the registration clash check and creates a project, the other records a
 * reportable reason. The server refuses a bare move onto either, so the UI matching
 * that is honesty rather than duplication.
 */
const TABS = [
  { id: 'overview', label: 'Overview', icon: Info },
  { id: 'informant', label: 'Who told us', icon: UserRound },
  { id: 'handover', label: 'Handover', icon: ArrowRightLeft },
  { id: 'buyer', label: 'Buyer', icon: Building2 },
  { id: 'projects', label: 'Projects', icon: FolderKanban },
  { id: 'activity', label: 'Activity', icon: History },
] as const;

type TabId = (typeof TABS)[number]['id'];

/** One offer in the header: the primary button, or a row in the gear menu. */
type HeaderAction = {
  key: string;
  label: string;
  icon?: React.ReactNode;
  pending?: boolean;
  run: () => void;
};

export function LeadDetailClient({ leadId }: { leadId: string }) {
  const router = useRouter();
  const backHref = useBackToListHref('/project-sales/leads');
  const searchParams = useSearchParams();
  const requestedTab = searchParams.get('tab') as TabId | null;
  const activeTab: TabId =
    requestedTab && TABS.some((tab) => tab.id === requestedTab) ? requestedTab : 'overview';

  const { data: lead, isLoading, isError, error } = useLead(leadId);
  const graph = useProjectStatusGraph('project_lead');
  const { move, disqualify, reopen, remove, update } = useLeadMutations();
  const { assign, accept, decline } = useLeadAcceptanceMutations();
  const { data: session } = useSession();
  const [qualifying, setQualifying] = React.useState(false);
  const [disqualifying, setDisqualifying] = React.useState(false);
  const [assigning, setAssigning] = React.useState(false);
  const [declining, setDeclining] = React.useState(false);
  const [editingWho, setEditingWho] = React.useState(false);
  const [confirmDelete, setConfirmDelete] = React.useState(false);

  function selectTab(tab: TabId) {
    const next = new URLSearchParams(searchParams.toString());
    if (tab === 'overview') next.delete('tab');
    else next.set('tab', tab);
    const query = next.toString();
    router.replace(`/project-sales/leads/${leadId}${query ? `?${query}` : ''}`, {
      scroll: false,
    });
  }

  if (isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-10 w-2/3" />
        <Skeleton className="h-9 w-full" />
        <Skeleton className="h-48 w-full" />
      </div>
    );
  }

  if (isError || !lead) {
    return (
      <div className="rounded-lg border border-destructive/40 bg-destructive/5 px-6 py-10 text-center">
        <h2 className="text-sm font-semibold text-destructive">
          This lead could not be loaded
        </h2>
        <p className="mx-auto mt-1 max-w-md text-sm text-muted-foreground">
          {error instanceof Error ? error.message : 'It may have been deleted.'}
        </p>
        <Button asChild variant="outline" className="mt-4">
          <Link href="/project-sales/leads">Back to leads</Link>
        </Button>
      </div>
    );
  }

  const isOpen = lead.outcome === 'open';

  // Phase 1's ProjectLead predates the informant and the handshake, so the P1 fields are
  // read through the wider type. The dialogs below still take the phase-1 shape.
  const view: LeadWithAcceptance = lead;
  const viewerId = session?.user?.id;
  // Accept and decline belong to the person holding it. Anyone else pressing them would
  // get a 403, which is a worse way to learn the same thing.
  const isAssignee = Boolean(viewerId && view.owner_user_id === viewerId);
  const awaitingAcceptance = view.acceptance_state === 'assigned';
  // Shared with the leads list, so the row and this header can never disagree about
  // who is allowed to hand a lead on.
  const canAssign = canAssignLead(view);

  // Terminal rungs are excluded on purpose: Qualified and Disqualified each do work the
  // rung alone cannot (one runs the clash check and registers a project, the other records
  // a reportable reason), so they belong to their dialogs and the server refuses a bare
  // move onto either.
  const stageMoves = lead.can_edit
    ? availableStatusMoves(graph.data, lead.status_id).filter((m) => !m.toIsTerminal)
    : [];

  /**
   * The ONE action, chosen by where the lead actually is.
   *
   * Order is the order the work happens in: a lead nobody has answered needs answering
   * before it can be qualified, and an unheld one needs an owner before anything else.
   * Everything not chosen here is still reachable, behind the gear.
   */
  const primaryAction: HeaderAction | null = (() => {
    if (lead.can_edit && isOpen && awaitingAcceptance && isAssignee) {
      return {
        key: 'accept',
        label: 'Accept',
        icon: <Check className="size-4" aria-hidden />,
        pending: accept.isPending,
        run: () => accept.mutate(lead.id),
      };
    }
    if (canAssign && !view.owner_user_id) {
      return {
        key: 'assign',
        label: 'Assign',
        icon: <UserPlus className="size-4" aria-hidden />,
        run: () => setAssigning(true),
      };
    }
    if (lead.can_edit && lead.outcome === 'disqualified') {
      return {
        key: 'reopen',
        label: 'Reopen',
        icon: <RotateCcw className="size-4" aria-hidden />,
        pending: reopen.isPending,
        run: () => reopen.mutate(lead.id),
      };
    }
    if (lead.can_edit && isOpen) {
      return { key: 'qualify', label: 'Qualify', run: () => setQualifying(true) };
    }
    return null;
  })();

  const secondaryActions: HeaderAction[] = [
    ...stageMoves.map((option) => ({
      key: option.transitionId,
      label: option.label,
      pending: move.isPending,
      run: () => move.mutate({ id: lead.id, toStatusId: option.toStatusId }),
    })),
    ...(lead.can_edit && isOpen && awaitingAcceptance && isAssignee
      ? [{ key: 'decline', label: 'Decline', run: () => setDeclining(true) }]
      : []),
    ...(canAssign && view.owner_user_id
      ? [{ key: 'reassign', label: 'Reassign', run: () => setAssigning(true) }]
      : []),
    ...(lead.can_edit && isOpen && primaryAction?.key !== 'qualify'
      ? [{ key: 'qualify', label: 'Qualify', run: () => setQualifying(true) }]
      : []),
    ...(lead.can_edit && isOpen
      ? [{ key: 'disqualify', label: 'Disqualify', run: () => setDisqualifying(true) }]
      : []),
  ];

  return (
    <div className="space-y-5">
      {/* flex-col until sm: a long title and the action buttons cannot share a row at
          phone width without overlapping and forcing page-wide horizontal overflow. */}
      <header className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="min-w-0 break-words">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm text-muted-foreground">{lead.lead_code}</span>
            {/* ONE pill. The outcome used to sit beside the rung, and since the outcome is
                DERIVED from the rung it read "Qualified Qualified". */}
            <LeadStatusPill statusKey={lead.status_key} label={lead.status_label} />
            {!lead.can_edit && <Badge variant="secondary">Read only</Badge>}
          </div>
          <h2 className="mt-1 text-xl font-semibold">{lead.title}</h2>
          <p className="text-sm text-muted-foreground">
            {[lead.developer_name, lead.location].filter(Boolean).join(' · ') || '-'}
          </p>
          <LeadAcceptanceBadge
            lead={view}
            className="mt-1.5 flex flex-wrap items-center gap-1.5"
          />
        </div>

        {/* Pager, gear, primary (D6). ONE named action, and everything else behind
            the gear: seven buttons across two rows - Accept, Decline, Reassign, a
            stage dropdown, Qualify, Disqualify, Delete - gave the commonest step no
            more weight than deleting the record, and the client's words were "I
            don't really know what each button do". */}
        <DetailActions
          data-testid="lead-header-actions"
          pager={{
            ...leadsPagerQuery,
            detailPath: '/project-sales/leads',
            currentId: lead.id,
            ariaLabel: 'lead',
          }}
          gear={
            (secondaryActions.length > 0 || lead.can_edit) && (
              <DetailActionsMenu ariaLabel="Lead actions">
                {secondaryActions.map((action) => (
                  <DropdownMenuItem
                    key={action.key}
                    disabled={action.pending}
                    onSelect={action.run}
                  >
                    {action.label}
                  </DropdownMenuItem>
                ))}
                {lead.can_edit && (
                  <DropdownMenuItem
                    variant="destructive"
                    onSelect={() => setConfirmDelete(true)}
                  >
                    <Trash2 className="size-4" aria-hidden />
                    Delete lead
                  </DropdownMenuItem>
                )}
              </DetailActionsMenu>
            )
          }
          primary={
            primaryAction && (
              <Button
                type="button"
                disabled={primaryAction.pending}
                onClick={primaryAction.run}
              >
                {primaryAction.icon}
                {primaryAction.label}
              </Button>
            )
          }
        />
      </header>

      {/* Above the strip, not inside a tab: it is true of the whole record, and somebody
          reading the buyer tab still needs to know two people recorded this development. */}
      {lead.possible_duplicates.length > 0 && (
        <div className="flex items-start gap-2 rounded-lg border border-border bg-muted/40 px-3 py-2.5 text-sm">
          <Users className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden />
          <p className="text-muted-foreground">
            {lead.possible_duplicates
              .map((hint) => hint.owner_name ?? hint.lead_code)
              .join(', ')}{' '}
            recorded this development too. Leads are not exclusive, so both stand. The
            first person to qualify it registers the project.
          </p>
        </div>
      )}

      {/* The shared strip rather than a hand-rolled `<nav>`: same scroller, same
          underline, and the keyboard behaviour the buttons never had.

          The sections are TabsContent inside the same `<Tabs>`, not siblings
          after it: a trigger points `aria-controls` at its panel, and with the
          panels outside it pointed at nothing and the sections themselves had
          no `role="tabpanel"`. Routing is unchanged - `value` still comes from
          the URL and `onValueChange` still writes it back, so each section is
          reached by its own link exactly as before, and only the open one
          mounts. */}
      <Tabs value={activeTab} onValueChange={(value) => selectTab(value as TabId)}>
        <TabsList aria-label="Lead sections">
          {TABS.map((tab) => (
            <TabsTrigger key={tab.id} value={tab.id}>
              <tab.icon />
              <span>{tab.label}</span>
            </TabsTrigger>
          ))}
        </TabsList>

        <TabsContent value="overview">
          <HeardCard lead={view} />
        </TabsContent>

        <TabsContent value="informant">
          <InformantCard
            lead={view}
            canEdit={lead.can_edit}
            onEdit={() => setEditingWho(true)}
          />
        </TabsContent>

        <TabsContent value="handover">
          <AcceptanceCard
            lead={view}
            canAssign={canAssign}
            onAssign={() => setAssigning(true)}
          />
        </TabsContent>

        <TabsContent value="buyer">
          <AccountPanel
            lead={view}
            canEdit={lead.can_edit}
            onSetBuyer={() => setEditingWho(true)}
          />
        </TabsContent>

        <TabsContent value="projects">
          <QualifiedProjects lead={view} />
        </TabsContent>

        <TabsContent value="activity">
          <LeadTimelinePanel lead={view} />
        </TabsContent>
      </Tabs>

      {qualifying && (
        <QualifyLeadDialog lead={lead} onDone={() => setQualifying(false)} />
      )}
      {disqualifying && (
        <DisqualifyLeadDialog
          lead={lead}
          onDone={() => setDisqualifying(false)}
          onConfirm={async (reason) => {
            await disqualify.mutateAsync({ id: lead.id, reason });
          }}
        />
      )}
      {assigning && (
        <AssignLeadDialog
          leadCode={lead.lead_code}
          currentOwnerName={lead.owner_name}
          submitting={assign.isPending}
          onDone={() => setAssigning(false)}
          onConfirm={async (ownerUserId, note) => {
            await assign.mutateAsync({ id: lead.id, ownerUserId, note });
          }}
        />
      )}
      {declining && (
        <DeclineLeadDialog
          leadCode={lead.lead_code}
          submitting={decline.isPending}
          onDone={() => setDeclining(false)}
          onConfirm={async (reason) => {
            await decline.mutateAsync({ id: lead.id, reason });
          }}
        />
      )}
      {editingWho && (
        <EditLeadInformantDialog
          lead={view}
          submitting={update.isPending}
          onDone={() => setEditingWho(false)}
          onConfirm={async (body) => {
            await update.mutateAsync({ id: lead.id, body });
          }}
        />
      )}

      <ConfirmDeleteDialog
        open={confirmDelete}
        onOpenChange={setConfirmDelete}
        title="Confirm delete"
        description={`Delete ${lead.lead_code} "${lead.title}"? This action cannot be undone. Any project already registered from it is kept.`}
        onDelete={async () => {
          await remove.mutateAsync(lead.id);
        }}
        onSuccess={() => router.push(backHref)}
        successMessage="Lead deleted"
      />

      {move.isPending && (
        <p className="flex items-center gap-2 text-xs text-muted-foreground">
          <Loader2 className="size-3 animate-spin" aria-hidden />
          Moving stage…
        </p>
      )}
    </div>
  );
}

/** The sighting itself: what was said, and what it was worth if anybody guessed. */
function HeardCard({ lead }: { lead: LeadWithAcceptance }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">What we heard</CardTitle>
      </CardHeader>
      <CardContent>
        <dl className="grid gap-x-6 gap-y-3 sm:grid-cols-2">
          <Fact label="Developer" value={lead.developer_name} />
          <Fact label="Location" value={lead.location} />
          <Fact
            label="Source"
            value={lead.source ? lead.source.replace(/_/g, ' ') : null}
          />
          <Fact label="Who said what" value={lead.source_detail} />
          <Fact
            label="Rough value"
            value={lead.estimated_value ? formatMyr(lead.estimated_value) : null}
          />
          <Fact
            label="Disqualified because"
            value={
              lead.disqualified_reason
                ? lead.disqualified_reason.replace(/_/g, ' ')
                : null
            }
          />
        </dl>
        {lead.notes && (
          <p className="mt-4 break-words rounded-md bg-muted/60 px-3 py-2 text-sm">
            {lead.notes}
          </p>
        )}
      </CardContent>
    </Card>
  );
}

/**
 * Who told us. Its own tab, apart from the buyer, so the two are never read as one thing:
 * an informant is a data source and never issues a purchase order.
 */
function InformantCard({
  lead,
  canEdit,
  onEdit,
}: {
  lead: LeadWithAcceptance;
  canEdit: boolean;
  onEdit: () => void;
}) {
  return (
    <Card>
      <CardHeader className="flex flex-col items-start gap-2 sm:flex-row sm:items-center sm:justify-between">
        <CardTitle className="min-w-0 break-words text-sm">Who told us</CardTitle>
        {canEdit && (
          <Button type="button" variant="outline" size="sm" onClick={onEdit}>
            <Pencil className="size-4" aria-hidden />
            Edit
          </Button>
        )}
      </CardHeader>
      <CardContent className="space-y-3">
        <dl className="grid gap-x-6 gap-y-3 sm:grid-cols-2">
          <Fact label="Source" value={informantSourceLabel(lead.informant_source)} />
          <Fact label="Their reference" value={lead.informant_ref} />
          <Fact label="Firm" value={lead.informant_party_label} />
          <Fact label="Contact name" value={lead.informant_contact_name} />
          <Fact label="Buyer" value={lead.customer_name} />
        </dl>
      </CardContent>
    </Card>
  );
}

/** The handshake: who holds it, since when, and what they said if they said no. */
function AcceptanceCard({
  lead,
  canAssign,
  onAssign,
}: {
  lead: LeadWithAcceptance;
  canAssign: boolean;
  onAssign: () => void;
}) {
  const unheld = !lead.owner_user_id;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">Handover</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <LeadAcceptanceBadge lead={lead} className="flex flex-wrap items-center gap-1.5" />
        <dl className="grid gap-x-6 gap-y-3 sm:grid-cols-2">
          <Fact label="Assigned to" value={lead.owner_name} />
          <Fact
            label="Assigned"
            value={
              lead.assigned_at ? formatDateTimeInMalaysia(lead.assigned_at) : null
            }
          />
          <Fact
            label="Accepted"
            value={
              lead.accepted_at ? formatDateTimeInMalaysia(lead.accepted_at) : null
            }
          />
          <Fact
            label="Declined"
            value={
              lead.declined_at ? formatDateTimeInMalaysia(lead.declined_at) : null
            }
          />
          <Fact label="Declined because" value={lead.declined_reason} />
        </dl>
        {/* The way out of the unheld state. "Assigned to -" above already says nobody
            holds it, so the sentence that used to explain that is gone. */}
        {unheld && canAssign && (
          <div className="border-t border-border pt-3">
            <Button type="button" variant="outline" size="sm" onClick={onAssign}>
              <UserPlus className="size-4" aria-hidden />
              Assign it
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

/**
 * What this lead became, as a list.
 *
 * Was a `ul` of links with a Badge on the right, plus a paragraph explaining what qualifying
 * does. Both are gone: the rows are the system list, and the row itself opens the project.
 */
function QualifiedProjects({ lead }: { lead: LeadWithAcceptance }) {
  const router = useRouter();
  const portfolio = useCustomerPortfolio(lead.customer_id ?? undefined);
  const projects = React.useMemo(
    () => (portfolio.data?.projects ?? []).filter((project) => project.lead_id === lead.id),
    [portfolio.data, lead.id],
  );

  const columns = React.useMemo<ColumnDef<Project>[]>(
    () => [
      {
        id: 'project_code',
        accessorFn: (row) => row.project_code,
        header: ({ column }) => <DataGridColumnHeader title="Project" column={column} />,
        cell: ({ row }) => (
          <span className="truncate text-sm" title={row.original.project_code}>
            {row.original.project_code}
          </span>
        ),
        size: 140,
        meta: { headerTitle: 'Project' },
      },
      {
        id: 'title',
        accessorFn: (row) => row.title,
        header: ({ column }) => <DataGridColumnHeader title="Title" column={column} />,
        cell: ({ row }) => (
          <span className="truncate text-sm" title={row.original.title}>
            {row.original.title}
          </span>
        ),
        size: 320,
        meta: { headerTitle: 'Title' },
      },
      {
        id: 'status',
        accessorFn: (row) => row.status_label ?? row.outcome,
        header: ({ column }) => <DataGridColumnHeader title="Stage" column={column} />,
        cell: ({ row }) => (
          <ProjectStatusPill
            statusKey={row.original.status_key}
            label={row.original.status_label}
          />
        ),
        size: 150,
        meta: { headerTitle: 'Stage' },
      },
    ],
    [],
  );

  return (
    <PanelDataGrid
      title="Projects from this lead"
      columns={columns}
      rows={projects}
      getRowId={(row) => row.id}
      listingKey="projects.leads.view::lead-projects"
      isLoading={portfolio.isLoading}
      error={portfolio.isError ? portfolio.error : undefined}
      emptyTitle={
        lead.project_count > 0
          ? `${lead.project_count} project${lead.project_count === 1 ? '' : 's'} came from this lead`
          : 'Nothing registered from this lead yet'
      }
      onRowClick={(row) => router.push(`/project-sales/${row.id}`)}
    />
  );
}

/**
 * The account view (AC-O9), from the lead's side.
 *
 * Two system lists, not two bulleted `ul`s with a sentence under each. The card subtitle
 * ("Everything this buyer has been part of, and what it turned into") and both empty
 * sentences are gone: the list headings already say what each list is.
 */
function AccountPanel({
  lead,
  canEdit,
  onSetBuyer,
}: {
  lead: LeadWithAcceptance;
  canEdit: boolean;
  onSetBuyer: () => void;
}) {
  const router = useRouter();
  const portfolio = useCustomerPortfolio(lead.customer_id ?? undefined);
  const otherLeads = React.useMemo(
    () => (portfolio.data?.leads ?? []).filter((row) => row.id !== lead.id),
    [portfolio.data, lead.id],
  );
  const projects = portfolio.data?.projects ?? [];

  const leadColumns = React.useMemo<ColumnDef<ProjectLead>[]>(
    () => [
      {
        id: 'lead_code',
        accessorFn: (row) => row.lead_code,
        header: ({ column }) => <DataGridColumnHeader title="Lead" column={column} />,
        cell: ({ row }) => <span className="truncate text-sm">{row.original.lead_code}</span>,
        size: 140,
        meta: { headerTitle: 'Lead' },
      },
      {
        id: 'title',
        accessorFn: (row) => row.title,
        header: ({ column }) => <DataGridColumnHeader title="Title" column={column} />,
        cell: ({ row }) => (
          <span className="truncate text-sm" title={row.original.title}>
            {row.original.title}
          </span>
        ),
        size: 320,
        meta: { headerTitle: 'Title' },
      },
      {
        id: 'status',
        accessorFn: (row) => row.status_label ?? row.outcome,
        header: ({ column }) => <DataGridColumnHeader title="Stage" column={column} />,
        cell: ({ row }) => (
          <LeadStatusPill statusKey={row.original.status_key} label={row.original.status_label} />
        ),
        size: 150,
        meta: { headerTitle: 'Stage' },
      },
    ],
    [],
  );

  const projectColumns = React.useMemo<ColumnDef<Project>[]>(
    () => [
      {
        id: 'project_code',
        accessorFn: (row) => row.project_code,
        header: ({ column }) => <DataGridColumnHeader title="Project" column={column} />,
        cell: ({ row }) => (
          <span className="truncate text-sm">{row.original.project_code}</span>
        ),
        size: 140,
        meta: { headerTitle: 'Project' },
      },
      {
        id: 'title',
        accessorFn: (row) => row.title,
        header: ({ column }) => <DataGridColumnHeader title="Title" column={column} />,
        cell: ({ row }) => (
          <span className="truncate text-sm" title={row.original.title}>
            {row.original.title}
          </span>
        ),
        size: 320,
        meta: { headerTitle: 'Title' },
      },
      {
        id: 'status',
        accessorFn: (row) => row.status_label ?? row.outcome,
        header: ({ column }) => <DataGridColumnHeader title="Stage" column={column} />,
        cell: ({ row }) => (
          <ProjectStatusPill
            statusKey={row.original.status_key}
            label={row.original.status_label}
          />
        ),
        size: 150,
        meta: { headerTitle: 'Stage' },
      },
    ],
    [],
  );

  if (!lead.customer_id) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">No buyer yet</CardTitle>
        </CardHeader>
        <CardContent>
          {canEdit ? (
            <Button type="button" variant="outline" size="sm" onClick={onSetBuyer}>
              Set the buyer
            </Button>
          ) : (
            <p className="text-sm text-muted-foreground">-</p>
          )}
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-4">
      <PanelDataGrid
        title={`${lead.customer_name ?? 'This buyer'} - other leads`}
        columns={leadColumns}
        rows={otherLeads}
        getRowId={(row) => row.id}
        listingKey="projects.leads.view::buyer-other-leads"
        isLoading={portfolio.isLoading}
        error={portfolio.isError ? portfolio.error : undefined}
        emptyTitle="No other leads on this buyer"
        onRowClick={(row) => router.push(`/project-sales/leads/${row.id}`)}
      />
      <PanelDataGrid
        title={`${lead.customer_name ?? 'This buyer'} - projects`}
        columns={projectColumns}
        rows={projects}
        getRowId={(row) => row.id}
        listingKey="projects.leads.view::buyer-projects"
        isLoading={portfolio.isLoading}
        error={portfolio.isError ? portfolio.error : undefined}
        emptyTitle="No projects on this buyer"
        onRowClick={(row) => router.push(`/project-sales/${row.id}`)}
      />
    </div>
  );
}

/**
 * No `emptyText` prop, deliberately. Every caller that had one was using it to write a
 * sentence into a table of facts ("No firm on record", "Not accepted yet"), which is the
 * thing ADR 1e bans: an unknown value is `-`.
 */
function Fact({ label, value }: { label: string; value?: string | null }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="break-words text-sm">
        {value ?? <span className="text-muted-foreground">-</span>}
      </dd>
    </div>
  );
}

function formatMyr(value: string): string {
  const amount = Number(value);
  if (Number.isNaN(amount)) return value;
  return `RM ${amount.toLocaleString('en-MY', { maximumFractionDigits: 2 })}`;
}
