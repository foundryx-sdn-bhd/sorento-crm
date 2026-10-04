'use client';

import * as React from 'react';
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import {
  CalendarRange,
  Clock,
  FileText,
  Flame,
  HandCoins,
  History,
  Info,
  ListChecks,
  Package,
  Paperclip,
  Pencil,
  Receipt,
  ShoppingCart,
  Trash2,
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
import { useProjectStatusGraph } from '@/app/(protected)/project-sales/_shared/hooks/useProjectStatusGraph';
import {
  projectsPagerQuery,
  useChangeProjectStatus,
  useDeleteProject,
  useProject,
} from '../../_shared/hooks/useProjects';
import EntityActivitiesLayout from '@/components/common/ActivitiesNotesPanel/EntityActivitiesLayout';
import { STALE_TONE_CLASS, describeStaleness } from '../../_shared/lib/staleness';
import { CriticalPanel } from './CriticalPanel';
import {
  ProjectStatusAction,
  availableStatusMoves,
  splitStatusMoves,
} from './ProjectStatusAction';
import { ProjectStatusPill } from './ProjectStatusPill';
import { ProjectActivityPanel } from './ProjectActivityPanel';
import { ProjectDocumentsPanel } from './ProjectDocumentsPanel';
import { ProjectAccessPanel } from './ProjectAccessPanel';
import { DeliverySchedulesPanel } from './DeliverySchedulesPanel';
import { PurchaseOrdersPanel } from './PurchaseOrdersPanel';
import { SalesOrdersPanel } from './SalesOrdersPanel';
import { QuotationsPanel } from './QuotationsPanel';
import { SamplesPanel } from './SamplesPanel';
import { SponsorshipsPanel } from './SponsorshipsPanel';
import { StakeholdersPanel } from './StakeholdersPanel';
import { TasksPanel } from './TasksPanel';

/**
 * Project detail as URL-routed tabs (AC-G6).
 *
 * Every tab renders even when it has nothing in it, with an explicit next step. A tab
 * that disappears when empty makes the feature look absent rather than unused, and the
 * user cannot discover what they are supposed to do next.
 *
 * Tabs whose slice has not shipped say so plainly instead of pretending: an honest
 * "arrives with quotations" beats a stub that looks broken.
 */
const TABS = [
  { id: 'overview', label: 'Overview', icon: Info },
  { id: 'stakeholders', label: 'Stakeholders', icon: Users },
  { id: 'tasks', label: 'Tasks', icon: ListChecks },
  { id: 'quotations', label: 'Quotations', icon: FileText },
  { id: 'samples', label: 'Samples', icon: Package },
  { id: 'sponsorships', label: 'Sponsorships', icon: HandCoins },
  { id: 'pos', label: 'POs', icon: ShoppingCart },
  { id: 'schedules', label: 'Delivery schedules', icon: CalendarRange },
  { id: 'sales-orders', label: 'Sales orders', icon: Receipt },
  { id: 'activity', label: 'Activity', icon: History },
  { id: 'documents', label: 'Documents', icon: Paperclip },
] as const;

type TabId = (typeof TABS)[number]['id'];

export function ProjectDetailClient({ projectId }: { projectId: string }) {
  const router = useRouter();
  const backHref = useBackToListHref('/project-sales/pipeline');
  const searchParams = useSearchParams();
  const requestedTab = searchParams.get('tab') as TabId | null;
  const activeTab: TabId =
    requestedTab && TABS.some((tab) => tab.id === requestedTab) ? requestedTab : 'overview';

  const { data: project, isLoading, isError, error } = useProject(projectId);
  const graph = useProjectStatusGraph('project', project?.template_id ?? null);
  const move = useChangeProjectStatus();
  const remove = useDeleteProject();
  const [confirmDelete, setConfirmDelete] = React.useState(false);

  function selectTab(tab: TabId) {
    const next = new URLSearchParams(searchParams.toString());
    if (tab === 'overview') next.delete('tab');
    else next.set('tab', tab);
    const query = next.toString();
    router.replace(`/project-sales/${projectId}${query ? `?${query}` : ''}`, {
      scroll: false,
    });
  }

  if (isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-10 w-2/3" />
        <Skeleton className="h-9 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  if (isError || !project) {
    return (
      <div className="rounded-lg border border-destructive/40 bg-destructive/5 px-6 py-10 text-center">
        <h2 className="text-sm font-semibold text-destructive">
          This project could not be loaded
        </h2>
        <p className="mx-auto mt-1 max-w-md text-sm text-muted-foreground">
          {error instanceof Error ? error.message : 'It may have been deleted.'}
        </p>
        <Button asChild variant="outline" className="mt-4">
          <Link href="/project-sales/pipeline">Back to the pipeline</Link>
        </Button>
      </div>
    );
  }

  const stale = describeStaleness(project);
  const moves = project.can_edit
    ? availableStatusMoves(graph.data, project.status_id)
    : [];
  const { secondary: secondaryMoves } = splitStatusMoves(moves);

  return (
    // The shared drawer (AC-H1): posting, @-mentions and internal notes come from the same
    // component tickets use, against the project adapter. Wrapping here rather than inside
    // the Activity tab so the composer is reachable from every tab -- the moment somebody
    // wants to record what a developer just said is rarely while looking at the feed.
    <EntityActivitiesLayout entityType="project" entityId={project.id}>
    {/* `pb-64` is for the dropdowns, not for looks. A picker on the last row of the last
        panel sat flush against the end of the document, so the popover had a few dozen
        pixels of viewport under it, shrank to two visible options, and there was nothing
        below to scroll to - the client's words: "i am stuck, i can't scroll down". Trailing
        space means an inline picker always has somewhere to open into. */}
    <div className="space-y-5 pb-64">
      {/* flex-col until sm: a long title and the action buttons cannot share a row at
          phone width without overlapping and forcing page-wide horizontal overflow. */}
      <header className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="min-w-0 break-words">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm text-muted-foreground">
              {project.project_code}
            </span>
            {/* ONE pill. The outcome is DERIVED from the rung (a project on Lost reads
                outcome "lost"), so showing both put "Registered Open" and, on the lead,
                "Qualified Qualified" side by side. See components/common/StatusPill. */}
            <ProjectStatusPill
              statusKey={project.status_key}
              label={project.status_label}
            />
            {project.is_critical && (
              <Badge variant="destructive" className="gap-1">
                <Flame className="size-3" aria-hidden />
                Critical
              </Badge>
            )}
            {stale && (
              <Badge
                variant="outline"
                className={`gap-1 ${STALE_TONE_CLASS[stale.tone as 'notice']}`}
                title={stale.detail}
              >
                <Clock className="size-3" aria-hidden />
                {stale.label}
              </Badge>
            )}
            {!project.can_edit && <Badge variant="outline">Read only</Badge>}
          </div>
          <h2 className="mt-1 text-xl font-semibold">{project.title}</h2>
          <p className="text-sm text-muted-foreground">
            {[project.developer_name, project.location].filter(Boolean).join(' · ') ||
              'No developer or location recorded yet'}
          </p>
        </div>

        {/* Pager, gear, primary (D6). One primary action, and everything else behind
            the gear: Delete used to sit here in full, weighing the same as the step
            the person came to take. */}
        <DetailActions
          data-testid="project-header-actions"
          pager={{
            ...projectsPagerQuery,
            detailPath: '/project-sales',
            currentId: project.id,
            ariaLabel: 'project',
          }}
          gear={
            project.can_edit && (
              <DetailActionsMenu ariaLabel="Project actions">
                {/* First item, never a second primary (AC-PF005, D9): the form is the
                    one place every field on this page is edited, so it leads the menu
                    rather than sitting after the status moves. */}
                <DropdownMenuItem
                  onSelect={() => router.push(`/project-sales/${project.id}/edit`)}
                >
                  <Pencil className="size-4" aria-hidden />
                  Edit project
                </DropdownMenuItem>
                {/* Exits and side moves. Deliberately not in the header: marking a
                    pursuit lost should never sit one careless click from advancing it. */}
                {secondaryMoves.map((option) => (
                  <DropdownMenuItem
                    key={option.transitionId}
                    disabled={move.isPending}
                    onSelect={() =>
                      move.mutate({ projectId: project.id, toStatusId: option.toStatusId })
                    }
                  >
                    {option.label}
                  </DropdownMenuItem>
                ))}
                <DropdownMenuItem
                  variant="destructive"
                  onSelect={() => setConfirmDelete(true)}
                >
                  <Trash2 className="size-4" aria-hidden />
                  Delete project
                </DropdownMenuItem>
              </DetailActionsMenu>
            )
          }
          primary={
            <ProjectStatusAction
              moves={moves}
              isPending={move.isPending}
              onMove={(toStatusId) =>
                move.mutate({ projectId: project.id, toStatusId })
              }
            />
          }
        />
      </header>

      {/* The ladder says its own reason and its own consequence (AC-H6). A badge alone
          leaves the reader guessing what "Unattended" changed; this states it, and the
          takeover route is a click away in Access on the Overview tab. */}
      {stale && (
        <div
          className={
            stale.level >= 3
              ? 'rounded-lg border border-destructive/40 bg-destructive/5 px-4 py-3'
              : 'rounded-lg border border-amber-300 bg-amber-50/60 px-4 py-3 dark:bg-amber-950/20'
          }
        >
          <p className="text-sm font-medium">{stale.label}</p>
          <p className="text-sm text-muted-foreground">{stale.detail}</p>
        </div>
      )}

      {/* The shared strip, not a hand-rolled `<nav>` of buttons: it already owns
          the scroller, the underline, the pressed state and the roving-focus
          keyboard behaviour this reimplemented without.

          The sections are TabsContent inside the same `<Tabs>`, not siblings
          after it: a trigger points `aria-controls` at its panel, and with the
          panels outside it pointed at nothing while the sections carried no
          `role="tabpanel"`. Routing is unchanged - `value` still comes from the
          URL and `onValueChange` still writes it back, so each section keeps
          its own link and only the open one mounts. */}
      <Tabs value={activeTab} onValueChange={(value) => selectTab(value as TabId)}>
        <TabsList aria-label="Project sections">
          {TABS.map((tab) => (
            <TabsTrigger key={tab.id} value={tab.id}>
              <tab.icon />
              <span>{tab.label}</span>
            </TabsTrigger>
          ))}
        </TabsList>
        <TabsContent value="overview">
          {/* Four titled sections rather than one fifteen-field grid. The old
              single "Registration" card put the developer, the contract value
              and the originating lead in one undifferentiated list, so finding
              any one of them meant reading all of them, and the lead link was
              invisible at the bottom. */}
          <div className="grid gap-4 lg:grid-cols-3">
            <div className="space-y-4 lg:col-span-2">
              <Section title="The development">
                <Fact label="Developer" value={project.developer_name} />
                <Fact label="Registered company / SPV" value={project.registered_company_name} />
                <Fact label="Location" value={project.location} />
                <Fact label="Address" value={project.address} />
                <Fact label="Project type" value={project.type_name} />
                <Fact label="Template" value={project.template_name} />
                <Fact label="Filing reference" value={project.admin_ref} />
              </Section>

              <Section title="Value and timing">
                <Fact
                  label="Estimated sales value"
                  value={
                    project.estimated_sales_value
                      ? formatMyr(project.estimated_sales_value)
                      : null
                  }
                />
                <Fact label="Launch date" value={formatDate(project.launch_date)} />
                <Fact
                  label="Expected delivery"
                  value={
                    project.expected_delivery_from || project.expected_delivery_to
                      ? [
                          formatDate(project.expected_delivery_from),
                          formatDate(project.expected_delivery_to),
                        ]
                          .filter(Boolean)
                          .join(' - ')
                      : null
                  }
                />
                <Fact
                  label="Brands"
                  value={project.brands.length ? project.brands.join(', ') : null}
                />
              </Section>

              <Section title="Consultants">
                <Fact label="Architect" value={project.architect_name} />
                <Fact label="Main contractor" value={project.main_contractor_name} />
              </Section>

              <CriticalPanel project={project} />
            </div>

            <div className="space-y-4">
              {/* AC-O10, and its own card because "which lead did this come from" is a
                  question people ask directly. As one more Fact in a long grid it read as
                  filing metadata and got missed. "Registered directly" is a real answer,
                  not a missing one: a tender notice claimed the same hour never had a lead. */}
              <Card>
                <CardHeader>
                  <CardTitle className="text-sm">Where this came from</CardTitle>
                </CardHeader>
                <CardContent>
                  {project.lead_id ? (
                    <dl className="grid gap-x-6 gap-y-3">
                      <div className="min-w-0">
                        <dt className="text-xs text-muted-foreground">Lead</dt>
                        <dd className="break-words text-sm font-medium">
                          <Link
                            href={`/project-sales/leads/${project.lead_id}`}
                            className="text-primary hover:underline"
                          >
                            {project.lead_code ?? 'View the lead'}
                          </Link>
                        </dd>
                      </div>
                      <Fact label="Lead source" value={labelise(project.lead_source)} />
                      <Fact label="Lead raised" value={formatDate(project.lead_created_at)} />
                    </dl>
                  ) : (
                    <p className="text-sm">Registered directly, with no lead before it.</p>
                  )}
                </CardContent>
              </Card>

              <ProjectAccessPanel project={project} />
            </div>
          </div>
        </TabsContent>

        <TabsContent value="stakeholders">
          <StakeholdersPanel project={project} />
        </TabsContent>
        <TabsContent value="tasks">
          <TasksPanel project={project} />
        </TabsContent>
        <TabsContent value="quotations">
          <QuotationsPanel project={project} />
        </TabsContent>
        <TabsContent value="samples">
          <SamplesPanel project={project} />
        </TabsContent>
        <TabsContent value="sponsorships">
          <SponsorshipsPanel project={project} />
        </TabsContent>
        <TabsContent value="pos">
          <PurchaseOrdersPanel project={project} />
        </TabsContent>
        <TabsContent value="schedules">
          <DeliverySchedulesPanel project={project} />
        </TabsContent>
        <TabsContent value="sales-orders">
          <SalesOrdersPanel project={project} />
        </TabsContent>
        <TabsContent value="activity">
          <ProjectActivityPanel project={project} />
        </TabsContent>
        <TabsContent value="documents">
          <ProjectDocumentsPanel project={project} />
        </TabsContent>
      </Tabs>

      <ConfirmDeleteDialog
        open={confirmDelete}
        onOpenChange={setConfirmDelete}
        title="Confirm delete"
        description={`Delete ${project.project_code} "${project.title}"? This action cannot be undone. If a customer purchase order has been recorded against it, archive it instead.`}
        onDelete={async () => {
          await remove.mutateAsync(project.id);
        }}
        onSuccess={() => router.push(backHref)}
        successMessage="Project deleted"
      />
    </div>
    </EntityActivitiesLayout>
  );
}

/** One titled group of facts. Two columns from sm up, one on a phone. */
function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">{title}</CardTitle>
      </CardHeader>
      <CardContent>
        <dl className="grid gap-x-6 gap-y-3 sm:grid-cols-2">{children}</dl>
      </CardContent>
    </Card>
  );
}

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

/** A stored code ("tender_notice") as a readable phrase. No dictionary to drift. */
function labelise(value?: string | null): string | null {
  if (!value) return null;
  const words = value.replace(/[_-]+/g, ' ').trim();
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : null;
}

function formatDate(iso?: string | null): string | null {
  if (!iso) return null;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleDateString('en-MY', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
  });
}
