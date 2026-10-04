'use client';

import * as React from 'react';
import type { ColumnDef } from '@tanstack/react-table';
import { LayoutGrid, Pencil, Plus, Rows3, Star, Trash2 } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Skeleton } from '@/components/ui/skeleton';
import { Textarea } from '@/components/ui/textarea';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { ConfirmDeleteDialog } from '@/components/common/ConfirmDeleteDialog';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { PanelDataGrid } from '@/components/common/PanelDataGrid';
import { ViewToggle } from '../../_shared/components/ViewToggle';
import {
  useProjectParties,
  useProjectTemplates,
  useStakeholders,
  useStakeholderMutations,
} from '../../_shared/hooks/useProjects';
import type {
  Project,
  ProjectStakeholder,
} from '../../_shared/types/project.types';

const INFLUENCE_OPTIONS = [
  { value: 'high', label: 'High' },
  { value: 'medium', label: 'Medium' },
  { value: 'low', label: 'Low' },
];

/**
 * The cast on this project: who decides, who influences, who tells us things.
 *
 * The role belongs to the person-on-THIS-project pairing, never to the person. The
 * same QS is a decision maker on one tender and an influencer on the next, so there is
 * no global contact master here and no attempt to build one.
 *
 * Roles come from the project's TEMPLATE, which is why a project with no template
 * shows an explicit prompt rather than an empty dropdown the user cannot fix.
 */
export function StakeholdersPanel({ project }: { project: Project }) {
  const stakeholders = useStakeholders(project.id);
  const { remove } = useStakeholderMutations(project.id);
  const [editing, setEditing] = React.useState<ProjectStakeholder | null>(null);
  const [creating, setCreating] = React.useState(false);
  const [deleting, setDeleting] = React.useState<ProjectStakeholder | null>(null);
  // List by default. Cards are a view somebody chooses (ADR 1d): the client's words were
  // "card can be a view, but the default view should be list".
  const [view, setView] = React.useState<'list' | 'cards'>('list');

  const templates = useProjectTemplates(project.type_id ?? undefined);
  const template = templates.data?.find((candidate) => candidate.id === project.template_id);
  const roles = (template?.roles ?? []).filter((role) => role.is_active);

  const rows = stakeholders.data ?? [];

  const columns = React.useMemo<ColumnDef<ProjectStakeholder>[]>(
    () => [
      {
        id: 'person_name',
        accessorFn: (row) => row.person_name,
        header: ({ column }) => <DataGridColumnHeader title="Name" column={column} />,
        cell: ({ row }) => (
          <div className="flex min-w-0 items-center gap-1.5">
            {row.original.is_primary && (
              <Star
                className="size-3.5 shrink-0 fill-amber-400 text-amber-500"
                aria-label="Primary contact"
              />
            )}
            <span className="truncate text-sm font-medium" title={row.original.person_name}>
              {row.original.person_name}
            </span>
          </div>
        ),
        size: 200,
        meta: { headerTitle: 'Name' },
      },
      {
        id: 'role_name',
        accessorFn: (row) => row.role_name ?? '',
        header: ({ column }) => <DataGridColumnHeader title="Role" column={column} />,
        cell: ({ row }) =>
          row.original.role_name ? (
            <Badge variant="secondary" appearance="light" className="text-[11px]">
              {row.original.role_name}
            </Badge>
          ) : (
            <span className="text-muted-foreground">-</span>
          ),
        size: 160,
        meta: { headerTitle: 'Role' },
      },
      {
        id: 'influence',
        accessorFn: (row) => row.influence ?? '',
        header: ({ column }) => <DataGridColumnHeader title="Influence" column={column} />,
        cell: ({ row }) =>
          row.original.influence ? (
            <span className="text-sm capitalize">{row.original.influence}</span>
          ) : (
            <span className="text-muted-foreground">-</span>
          ),
        size: 110,
        meta: { headerTitle: 'Influence' },
      },
      {
        id: 'firm',
        accessorFn: (row) => row.party_name ?? '',
        header: ({ column }) => <DataGridColumnHeader title="Firm" column={column} />,
        cell: ({ row }) =>
          row.original.party_name ? (
            <span className="truncate text-sm" title={row.original.party_name}>
              {row.original.party_name}
            </span>
          ) : (
            <span className="text-muted-foreground">-</span>
          ),
        size: 200,
        meta: { headerTitle: 'Firm' },
      },
      {
        id: 'job_title',
        accessorFn: (row) => row.job_title ?? '',
        header: ({ column }) => <DataGridColumnHeader title="Job title" column={column} />,
        cell: ({ row }) =>
          row.original.job_title ? (
            <span className="truncate text-sm" title={row.original.job_title}>
              {row.original.job_title}
            </span>
          ) : (
            <span className="text-muted-foreground">-</span>
          ),
        size: 170,
        meta: { headerTitle: 'Job title' },
      },
      {
        id: 'reach',
        accessorFn: (row) => [row.phone, row.email].filter(Boolean).join(' '),
        header: ({ column }) => <DataGridColumnHeader title="Phone / email" column={column} />,
        cell: ({ row }) => {
          const reach = [row.original.phone, row.original.email].filter(Boolean).join(' · ');
          return reach ? (
            <span className="truncate text-sm" title={reach}>
              {reach}
            </span>
          ) : (
            <span className="text-muted-foreground">-</span>
          );
        },
        size: 220,
        meta: { headerTitle: 'Phone / email' },
      },
      ...(project.can_edit
        ? [
            {
              id: 'actions',
              header: () => <span className="sr-only">Actions</span>,
              cell: ({ row }: { row: { original: ProjectStakeholder } }) => (
                <div className="flex justify-end gap-1">
                  <Button
                    mode="icon"
                    variant="ghost"
                    size="sm"
                    onClick={() => setEditing(row.original)}
                    aria-label={`Edit ${row.original.person_name}`}
                  >
                    <Pencil className="size-3.5" />
                  </Button>
                  <Button
                    mode="icon"
                    variant="ghost"
                    size="sm"
                    onClick={() => setDeleting(row.original)}
                    aria-label={`Remove ${row.original.person_name}`}
                  >
                    <Trash2 className="size-3.5 text-destructive" />
                  </Button>
                </div>
              ),
              size: 90,
              enableResizing: false,
              meta: { headerTitle: 'Actions' },
            } as ColumnDef<ProjectStakeholder>,
          ]
        : []),
    ],
    [project.can_edit],
  );

  const toolbar = (
    <>
      <ViewToggle
        ariaLabel="Stakeholder view"
        value={view}
        onChange={setView}
        options={[
          { value: 'list', icon: <Rows3 className="size-4" aria-hidden />, label: 'List view' },
          {
            value: 'cards',
            icon: <LayoutGrid className="size-4" aria-hidden />,
            label: 'Card view',
          },
        ]}
      />
      {project.can_edit && (
        <Button type="button" size="sm" onClick={() => setCreating(true)}>
          <Plus className="size-4" aria-hidden />
          Add stakeholder
        </Button>
      )}
    </>
  );

  return (
    <>
      {view === 'list' ? (
        <PanelDataGrid
          title="Stakeholders"
          toolbar={toolbar}
          columns={columns}
          rows={rows}
          getRowId={(row) => row.id}
          listingKey="projects.projects.view::project-stakeholders"
          isLoading={stakeholders.isLoading}
          error={stakeholders.isError ? stakeholders.error : undefined}
          emptyTitle="No stakeholders recorded"
        />
      ) : (
        <Card>
          <CardHeader className="flex flex-col items-start gap-3 sm:flex-row sm:items-center sm:justify-between">
            <CardTitle className="text-sm">Stakeholders</CardTitle>
            <div className="flex flex-wrap items-center gap-2">{toolbar}</div>
          </CardHeader>
          <CardContent>
            {stakeholders.isLoading ? (
              <div className="space-y-2">
                <Skeleton className="h-16 w-full" />
                <Skeleton className="h-16 w-full" />
              </div>
            ) : rows.length === 0 ? (
              <div className="rounded-lg border border-dashed border-border px-6 py-10 text-center">
                <h3 className="text-sm font-semibold">No stakeholders recorded</h3>
                {project.can_edit && (
                  <Button type="button" className="mt-4" onClick={() => setCreating(true)}>
                    <Plus className="size-4" aria-hidden />
                    Add the decision maker
                  </Button>
                )}
              </div>
            ) : (
              <ul className="grid gap-3 sm:grid-cols-2">
                {rows.map((stakeholder) => (
                  <li key={stakeholder.id} className="rounded-lg border border-border p-3">
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0 space-y-1">
                        <div className="flex flex-wrap items-center gap-1.5">
                          {stakeholder.is_primary && (
                            <Star
                              className="size-3.5 shrink-0 fill-amber-400 text-amber-500"
                              aria-label="Primary contact"
                            />
                          )}
                          <p className="truncate font-medium" title={stakeholder.person_name}>
                            {stakeholder.person_name}
                          </p>
                        </div>
                        <div className="flex flex-wrap gap-1.5">
                          {stakeholder.role_name && (
                            <Badge variant="secondary" appearance="light" className="text-[11px]">
                              {stakeholder.role_name}
                            </Badge>
                          )}
                          {stakeholder.influence && (
                            <Badge variant="secondary" appearance="light" className="text-[11px] capitalize">
                              {stakeholder.influence} influence
                            </Badge>
                          )}
                        </div>
                        {(stakeholder.job_title || stakeholder.party_name) && (
                          <p
                            className="truncate text-xs text-muted-foreground"
                            title={[stakeholder.job_title, stakeholder.party_name]
                              .filter(Boolean)
                              .join(', ')}
                          >
                            {[stakeholder.job_title, stakeholder.party_name]
                              .filter(Boolean)
                              .join(' · ')}
                          </p>
                        )}
                        {(stakeholder.phone || stakeholder.email) && (
                          <p className="truncate text-xs text-muted-foreground">
                            {[stakeholder.phone, stakeholder.email].filter(Boolean).join(' · ')}
                          </p>
                        )}
                        {stakeholder.notes && (
                          <p className="break-words pt-0.5 text-xs text-muted-foreground">
                            {stakeholder.notes}
                          </p>
                        )}
                      </div>
                      {project.can_edit && (
                        <div className="flex shrink-0 gap-1">
                          <Button
                            mode="icon"
                            variant="ghost"
                            size="sm"
                            onClick={() => setEditing(stakeholder)}
                            aria-label={`Edit ${stakeholder.person_name}`}
                          >
                            <Pencil className="size-3.5" />
                          </Button>
                          <Button
                            mode="icon"
                            variant="ghost"
                            size="sm"
                            onClick={() => setDeleting(stakeholder)}
                            aria-label={`Remove ${stakeholder.person_name}`}
                          >
                            <Trash2 className="size-3.5 text-destructive" />
                          </Button>
                        </div>
                      )}
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      )}

      {(creating || editing) && (
        <StakeholderFormDialog
          project={project}
          stakeholder={editing}
          roles={roles}
          onDone={() => {
            setCreating(false);
            setEditing(null);
          }}
        />
      )}

      <ConfirmDeleteDialog
        open={Boolean(deleting)}
        onOpenChange={(next) => !next && setDeleting(null)}
        title="Confirm remove"
        description={
          deleting
            ? `Remove ${deleting.person_name} from ${project.project_code}? This action cannot be undone.`
            : ''
        }
        onDelete={async () => {
          if (!deleting) return;
          await remove.mutateAsync(deleting.id);
        }}
        onSuccess={() => setDeleting(null)}
        successMessage="Stakeholder removed"
      />
    </>
  );
}

function StakeholderFormDialog({
  project,
  stakeholder,
  roles,
  onDone,
}: {
  project: Project;
  stakeholder: ProjectStakeholder | null;
  roles: { id: string; name: string }[];
  onDone: () => void;
}) {
  const { add, update } = useStakeholderMutations(project.id);
  const parties = useProjectParties({ limit: 200 });

  const [personName, setPersonName] = React.useState(stakeholder?.person_name ?? '');
  const [roleId, setRoleId] = React.useState(stakeholder?.role_id ?? '');
  const [partyId, setPartyId] = React.useState(stakeholder?.party_id ?? '');
  const [jobTitle, setJobTitle] = React.useState(stakeholder?.job_title ?? '');
  const [phone, setPhone] = React.useState(stakeholder?.phone ?? '');
  const [email, setEmail] = React.useState(stakeholder?.email ?? '');
  const [influence, setInfluence] = React.useState(stakeholder?.influence ?? '');
  const [isPrimary, setIsPrimary] = React.useState(stakeholder?.is_primary ?? false);
  const [notes, setNotes] = React.useState(stakeholder?.notes ?? '');

  const isEdit = Boolean(stakeholder);
  const pending = add.isPending || update.isPending;

  return (
    <Dialog open onOpenChange={(next) => !next && onDone()}>
      <DialogContent className="max-h-[92vh] w-full max-w-lg overflow-hidden">
        <DialogHeader>
          <DialogTitle>
            {isEdit ? `Edit ${stakeholder?.person_name}` : 'Add a stakeholder'}
          </DialogTitle>
          <DialogDescription>
            The role is per project. Someone can be a decision maker here and an
            influencer on the next tender.
          </DialogDescription>
        </DialogHeader>

        <form
          onSubmit={async (event) => {
            event.preventDefault();
            const body = {
              person_name: personName.trim(),
              role_id: roleId || null,
              party_id: partyId || null,
              job_title: jobTitle.trim() || null,
              phone: phone.trim() || null,
              email: email.trim() || null,
              influence: (influence || null) as never,
              is_primary: isPrimary,
              notes: notes.trim() || null,
            };
            if (stakeholder) {
              await update.mutateAsync({ id: stakeholder.id, body });
            } else {
              await add.mutateAsync(body);
            }
            onDone();
          }}
        >
          <DialogBody className="max-h-[65vh] space-y-4 overflow-y-auto">
            <div className="space-y-1.5">
              <Label htmlFor="stakeholder-name">
                Name <span className="text-destructive">*</span>
              </Label>
              <Input
                id="stakeholder-name"
                value={personName}
                onChange={(event) => setPersonName(event.target.value)}
                required
              />
            </div>

            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label htmlFor="stakeholder-role">Role</Label>
                {roles.length === 0 ? (
                  <p className="rounded-md border border-dashed border-border px-2.5 py-2 text-xs text-muted-foreground">
                    {project.template_id
                      ? 'This template offers no roles yet.'
                      : 'Set a project type and template on this project to choose from its roles.'}
                  </p>
                ) : (
                  <SearchableSelect
                    id="stakeholder-role"
                    value={roleId}
                    onChange={setRoleId}
                    clearable
                    options={roles.map((role) => ({ value: role.id, label: role.name }))}
                    placeholder="Select a role"
                  />
                )}
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="stakeholder-influence">Influence</Label>
                <SearchableSelect
                  id="stakeholder-influence"
                  value={influence}
                  onChange={setInfluence}
                  clearable
                  options={INFLUENCE_OPTIONS}
                  placeholder="-"
                />
              </div>

              <div className="space-y-1.5 sm:col-span-2">
                <Label htmlFor="stakeholder-party">Firm</Label>
                <SearchableSelect
                  id="stakeholder-party"
                  value={partyId}
                  onChange={setPartyId}
                  clearable
                  loadError={parties.error}
                  onRetry={() => void parties.refetch()}
                  options={(parties.data?.data ?? []).map((party) => ({
                    value: party.id,
                    label: party.name,
                    description: party.party_type.replace(/_/g, ' '),
                  }))}
                  placeholder="Search parties"
                  emptyMessage="No parties yet. Add one under Parties"
                />
                <p className="text-xs text-muted-foreground">
                  Optional. A lone informant with no firm records fine.
                </p>
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="stakeholder-job">Job title</Label>
                <Input
                  id="stakeholder-job"
                  value={jobTitle}
                  onChange={(event) => setJobTitle(event.target.value)}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="stakeholder-phone">Phone</Label>
                <Input
                  id="stakeholder-phone"
                  value={phone}
                  onChange={(event) => setPhone(event.target.value)}
                />
              </div>
              <div className="space-y-1.5 sm:col-span-2">
                <Label htmlFor="stakeholder-email">Email</Label>
                <Input
                  id="stakeholder-email"
                  type="email"
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                />
              </div>
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="stakeholder-notes">Notes</Label>
              <Textarea
                id="stakeholder-notes"
                value={notes}
                onChange={(event) => setNotes(event.target.value)}
                rows={3}
                placeholder="What they care about, who they report to, what they told us"
              />
            </div>

            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={isPrimary}
                onChange={(event) => setIsPrimary(event.target.checked)}
                className="size-4 rounded border-border"
              />
              Primary contact for this project
            </label>
          </DialogBody>

          <DialogFooter className="flex-col gap-2 sm:flex-row sm:justify-end">
            <Button type="button" variant="outline" onClick={onDone}>
              Cancel
            </Button>
            <Button type="submit" disabled={!personName.trim() || pending}>
              {isEdit ? 'Save changes' : 'Add stakeholder'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
