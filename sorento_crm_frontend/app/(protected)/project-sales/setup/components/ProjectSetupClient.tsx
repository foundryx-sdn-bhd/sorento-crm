'use client';

import * as React from 'react';
import {
  ColumnDef,
  PaginationState,
  SortingState,
  getCoreRowModel,
  getPaginationRowModel,
  getSortedRowModel,
  useReactTable,
} from '@tanstack/react-table';
import { CalendarClock, Check, Pencil, Plus, Trash2 } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardFooter, CardHeader, CardTable, CardTitle } from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridListToolbar } from '@/components/ui/data-grid-list-toolbar';
import { DataGridPagination } from '@/components/ui/data-grid-pagination';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { Skeleton } from '@/components/ui/skeleton';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { ConfirmDeleteDialog } from '@/components/common/ConfirmDeleteDialog';
import {
  useProjectTemplateMutations,
  useProjectTemplates,
  useProjectTypeMutations,
  useProjectTypes,
} from '../../_shared/hooks/useProjects';
import type { ProjectTemplate, ProjectType } from '../../_shared/types/project.types';
import { ProjectTypeDialog } from './ProjectTypeDialog';
import { ProjectTemplateDialog } from './ProjectTemplateDialog';
import { TemplateChecklistPanel } from './TemplateChecklistPanel';
import { PageHeader } from '@/components/common/PageHeader';

/**
 * Three levels of configuration on one screen, because they are only understandable
 * together: a TYPE (Hotel) holds TEMPLATES (New Build, Refurbishment), and a template
 * carries both the stakeholder ROLES it offers and the CHECKLIST a new project copies.
 *
 * Splitting these across three admin pages was the alternative. It hides the thing the
 * admin needs to see: which template a project gets, and therefore which checklist.
 *
 * Each level is a standard list with its own toolbar rather than a stack of cards with
 * floating icons, so the same reading rules apply here as on every other screen. The
 * master-detail link survives: picking a TYPE row filters the templates list, picking a
 * TEMPLATE row loads its checklist below. The picked row carries a tick in its first
 * column, which is the only thing a list needs to say "this one".
 */
export function ProjectSetupClient() {
  const types = useProjectTypes();
  const [selectedTypeId, setSelectedTypeId] = React.useState<string | null>(null);
  const templates = useProjectTemplates(selectedTypeId ?? undefined);
  const [selectedTemplateId, setSelectedTemplateId] = React.useState<string | null>(null);

  const typeMutations = useProjectTypeMutations();
  const templateMutations = useProjectTemplateMutations();

  const [typeDialog, setTypeDialog] = React.useState<{ type: ProjectType | null } | null>(null);
  const [templateDialog, setTemplateDialog] = React.useState<{
    template: ProjectTemplate | null;
  } | null>(null);
  const [deletingType, setDeletingType] = React.useState<ProjectType | null>(null);
  const [deletingTemplate, setDeletingTemplate] = React.useState<ProjectTemplate | null>(null);

  const typeRows = React.useMemo(() => types.data ?? [], [types.data]);
  const templateRows = React.useMemo(() => templates.data ?? [], [templates.data]);

  // Land on the first type so the screen is never three empty panes waiting on a click.
  React.useEffect(() => {
    if (!selectedTypeId && typeRows.length > 0) setSelectedTypeId(typeRows[0].id);
  }, [selectedTypeId, typeRows]);

  React.useEffect(() => {
    if (selectedTemplateId && !templateRows.some((row) => row.id === selectedTemplateId)) {
      setSelectedTemplateId(null);
    }
  }, [selectedTemplateId, templateRows]);

  const selectedTemplate = templateRows.find((row) => row.id === selectedTemplateId) ?? null;

  // Stable identities: the grids build their columns in a `useMemo` keyed on these, and
  // a fresh arrow per render would rebuild every column on every keystroke elsewhere.
  const addType = React.useCallback(() => setTypeDialog({ type: null }), []);
  const editType = React.useCallback((type: ProjectType) => setTypeDialog({ type }), []);
  const addTemplate = React.useCallback(() => setTemplateDialog({ template: null }), []);
  const editTemplate = React.useCallback(
    (template: ProjectTemplate) => setTemplateDialog({ template }),
    [],
  );
  const selectTemplate = React.useCallback(
    (id: string) => setSelectedTemplateId((previous) => (previous === id ? null : id)),
    [],
  );

  return (
    <div className="space-y-5">
      <PageHeader title="Project setup" />

      <div className="grid gap-4 xl:grid-cols-2">
        <ProjectTypesGrid
          rows={typeRows}
          isLoading={types.isLoading}
          error={types.error}
          isFetching={types.isFetching}
          selectedTypeId={selectedTypeId}
          onSelect={setSelectedTypeId}
          onRefresh={() => void types.refetch()}
          onAdd={addType}
          onEdit={editType}
          onDelete={setDeletingType}
        />

        <ProjectTemplatesGrid
          rows={templateRows}
          isLoading={Boolean(selectedTypeId) && templates.isLoading}
          error={templates.error}
          isFetching={templates.isFetching}
          hasType={Boolean(selectedTypeId)}
          selectedTemplateId={selectedTemplateId}
          onSelect={selectTemplate}
          onRefresh={() => void templates.refetch()}
          onAdd={addTemplate}
          onEdit={editTemplate}
          onDelete={setDeletingTemplate}
        />
      </div>

      {selectedTemplate ? (
        <TemplateChecklistPanel template={selectedTemplate} />
      ) : (
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">Starting checklist</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-sm text-muted-foreground">
              Select a template above to edit the checklist every new project of that
              template copies in.
            </p>
          </CardContent>
        </Card>
      )}

      {typeDialog && (
        <ProjectTypeDialog type={typeDialog.type} onDone={() => setTypeDialog(null)} />
      )}

      {templateDialog && selectedTypeId && (
        <ProjectTemplateDialog
          typeId={selectedTypeId}
          template={templateDialog.template}
          onDone={() => setTemplateDialog(null)}
        />
      )}

      <ConfirmDeleteDialog
        open={Boolean(deletingType)}
        onOpenChange={(next) => !next && setDeletingType(null)}
        title="Confirm delete"
        description={
          deletingType
            ? `Delete the project type "${deletingType.name}"? This action cannot be undone. A type still used by a template or a project cannot be deleted, so deactivate it instead.`
            : ''
        }
        onDelete={async () => {
          if (!deletingType) return;
          await typeMutations.remove.mutateAsync(deletingType.id);
        }}
        onSuccess={() => {
          if (deletingType?.id === selectedTypeId) setSelectedTypeId(null);
          setDeletingType(null);
        }}
        successMessage="Project type deleted"
      />

      <ConfirmDeleteDialog
        open={Boolean(deletingTemplate)}
        onOpenChange={(next) => !next && setDeletingTemplate(null)}
        title="Confirm delete"
        description={
          deletingTemplate
            ? `Delete the template "${deletingTemplate.name}"? This action cannot be undone, and its checklist goes with it. A template already used by a project cannot be deleted, so deactivate it instead.`
            : ''
        }
        onDelete={async () => {
          if (!deletingTemplate) return;
          await templateMutations.remove.mutateAsync(deletingTemplate.id);
        }}
        onSuccess={() => setDeletingTemplate(null)}
        successMessage="Template deleted"
      />
    </div>
  );
}

function ProjectTypesGrid({
  rows,
  isLoading,
  error,
  isFetching,
  selectedTypeId,
  onSelect,
  onRefresh,
  onAdd,
  onEdit,
  onDelete,
}: {
  rows: ProjectType[];
  isLoading: boolean;
  error?: unknown;
  isFetching: boolean;
  selectedTypeId: string | null;
  onSelect: (id: string) => void;
  onRefresh: () => void;
  onAdd: () => void;
  onEdit: (type: ProjectType) => void;
  onDelete: (type: ProjectType) => void;
}) {
  const [pagination, setPagination] = React.useState<PaginationState>({
    pageIndex: 0,
    pageSize: 10,
  });
  const [sorting, setSorting] = React.useState<SortingState>([
    { id: 'sort_order', desc: false },
  ]);

  const columns = React.useMemo<ColumnDef<ProjectType>[]>(
    () => [
      selectedMarkerColumn<ProjectType>(selectedTypeId),
      {
        accessorKey: 'name',
        header: ({ column }) => <DataGridColumnHeader title="Type" column={column} />,
        size: 220,
        meta: { headerTitle: 'Type', skeleton: <Skeleton className="h-4 w-32" /> },
        cell: ({ row }) => (
          <div className="min-w-0">
            <span className="block truncate font-medium" title={row.original.name}>
              {row.original.name}
            </span>
            <span
              className="block truncate text-xs text-muted-foreground"
              title={row.original.code}
            >
              {row.original.code}
            </span>
          </div>
        ),
      },
      {
        id: 'template_count',
        accessorFn: (row) => row.template_count ?? 0,
        header: ({ column }) => <DataGridColumnHeader title="Templates" column={column} />,
        size: 130,
        meta: { headerTitle: 'Templates', skeleton: <Skeleton className="h-4 w-14" /> },
        cell: ({ row }) => {
          const count = row.original.template_count ?? 0;
          return (
            <span className="tabular-nums">
              {count} template{count === 1 ? '' : 's'}
            </span>
          );
        },
      },
      {
        accessorKey: 'derives_delivery_from_launch',
        header: ({ column }) => <DataGridColumnHeader title="Delivery" column={column} />,
        size: 190,
        meta: { headerTitle: 'Delivery', skeleton: <Skeleton className="h-4 w-24" /> },
        cell: ({ row }) =>
          row.original.derives_delivery_from_launch ? (
            <Badge variant="secondary" className="gap-1">
              <CalendarClock className="size-3" aria-hidden />
              Delivery from launch
            </Badge>
          ) : (
            <span className="text-muted-foreground">Stated per project</span>
          ),
      },
      {
        accessorKey: 'is_active',
        header: ({ column }) => <DataGridColumnHeader title="Status" column={column} />,
        size: 110,
        meta: { headerTitle: 'Status', skeleton: <Skeleton className="h-4 w-16" /> },
        cell: ({ row }) =>
          row.original.is_active ? (
            <Badge variant="outline">Active</Badge>
          ) : (
            <Badge variant="secondary">Inactive</Badge>
          ),
      },
      {
        accessorKey: 'sort_order',
        header: ({ column }) => <DataGridColumnHeader title="Order" column={column} />,
        size: 90,
        meta: { headerTitle: 'Order', skeleton: <Skeleton className="h-4 w-8" /> },
        cell: ({ row }) => <span className="tabular-nums">{row.original.sort_order}</span>,
      },
      rowActionsColumn<ProjectType>({
        editLabel: (type) => `Edit ${type.name}`,
        deleteLabel: (type) => `Delete ${type.name}`,
        onEdit,
        onDelete,
      }),
    ],
    [selectedTypeId, onEdit, onDelete],
  );

  const table = useReactTable({
    columns,
    data: rows,
    getRowId: (row) => row.id,
    state: { pagination, sorting },
    onPaginationChange: setPagination,
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    columnResizeMode: 'onChange',
    enableColumnResizing: true,
    defaultColumn: { minSize: 44, maxSize: 800, size: 150 },
  });

  return (
    <DataGrid
      table={table}
      // Clicking a type row is what filters the templates list beside it.
      onRowClick={(row) => onSelect(row.id)}
      recordCount={rows.length}
      isLoading={isLoading}
      error={error}
      onRetry={onRefresh}
      listingKey="projects.types.view::types"
      tableLayout={{ width: 'fixed', columnsResizable: true, columnsVisibility: true }}
      emptyMessage={
        <span className="block max-w-md">
          <span className="block text-sm font-semibold text-foreground">
            No project types yet
          </span>
          A type is the kind of job: property development, hotel, fitout.
        </span>
      }
      // No emptyAction: the toolbar's Add is the one offer (#1335).
    >
      <Card>
        <CardHeader className="block pt-5">
          <CardTitle className="text-sm">Project types</CardTitle>
          <DataGridListToolbar
            table={table}
            exportConfig={{ filename: 'project_types_export.xlsx' }}
            onRefresh={onRefresh}
            isRefreshing={isFetching && !isLoading}
            primaryAction={
              <Button type="button" size="sm" onClick={onAdd}>
                <Plus className="size-4" aria-hidden />
                Add type
              </Button>
            }
          />
        </CardHeader>
        <CardTable>
          <DataGridTable />
        </CardTable>
        <CardFooter>
          <DataGridPagination />
        </CardFooter>
      </Card>
    </DataGrid>
  );
}

function ProjectTemplatesGrid({
  rows,
  isLoading,
  error,
  isFetching,
  hasType,
  selectedTemplateId,
  onSelect,
  onRefresh,
  onAdd,
  onEdit,
  onDelete,
}: {
  rows: ProjectTemplate[];
  isLoading: boolean;
  error?: unknown;
  isFetching: boolean;
  hasType: boolean;
  selectedTemplateId: string | null;
  onSelect: (id: string) => void;
  onRefresh: () => void;
  onAdd: () => void;
  onEdit: (template: ProjectTemplate) => void;
  onDelete: (template: ProjectTemplate) => void;
}) {
  const [pagination, setPagination] = React.useState<PaginationState>({
    pageIndex: 0,
    pageSize: 10,
  });
  const [sorting, setSorting] = React.useState<SortingState>([{ id: 'name', desc: false }]);

  const columns = React.useMemo<ColumnDef<ProjectTemplate>[]>(
    () => [
      selectedMarkerColumn<ProjectTemplate>(selectedTemplateId),
      {
        accessorKey: 'name',
        header: ({ column }) => <DataGridColumnHeader title="Template" column={column} />,
        size: 220,
        meta: { headerTitle: 'Template', skeleton: <Skeleton className="h-4 w-32" /> },
        cell: ({ row }) => (
          <span className="block truncate font-medium" title={row.original.name}>
            {row.original.name}
          </span>
        ),
      },
      {
        id: 'roles',
        accessorFn: (row) =>
          row.roles
            .filter((role) => role.is_active)
            .map((role) => role.name)
            .join(', '),
        header: ({ column }) => <DataGridColumnHeader title="Roles" column={column} />,
        size: 260,
        enableSorting: false,
        meta: { headerTitle: 'Roles', skeleton: <Skeleton className="h-4 w-36" /> },
        cell: ({ row }) => {
          const active = row.original.roles.filter((role) => role.is_active);
          const names = active.map((role) => role.name).join(', ');
          const text = `${active.length} roles: ${names || 'none'}`;
          return (
            <span className="block truncate" title={text}>
              {text}
            </span>
          );
        },
      },
      {
        accessorKey: 'has_forked_status_graph',
        header: ({ column }) => <DataGridColumnHeader title="Stages" column={column} />,
        size: 160,
        meta: { headerTitle: 'Stages', skeleton: <Skeleton className="h-4 w-20" /> },
        cell: ({ row }) =>
          row.original.has_forked_status_graph ? (
            <Badge variant="secondary">Own stage graph</Badge>
          ) : (
            <span className="text-muted-foreground">Shared stages</span>
          ),
      },
      {
        accessorKey: 'is_active',
        header: ({ column }) => <DataGridColumnHeader title="Status" column={column} />,
        size: 110,
        meta: { headerTitle: 'Status', skeleton: <Skeleton className="h-4 w-16" /> },
        cell: ({ row }) =>
          row.original.is_active ? (
            <Badge variant="outline">Active</Badge>
          ) : (
            <Badge variant="secondary">Inactive</Badge>
          ),
      },
      rowActionsColumn<ProjectTemplate>({
        editLabel: (template) => `Edit ${template.name}`,
        deleteLabel: (template) => `Delete ${template.name}`,
        onEdit,
        onDelete,
      }),
    ],
    [selectedTemplateId, onEdit, onDelete],
  );

  const table = useReactTable({
    columns,
    data: hasType ? rows : [],
    getRowId: (row) => row.id,
    state: { pagination, sorting },
    onPaginationChange: setPagination,
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    columnResizeMode: 'onChange',
    enableColumnResizing: true,
    defaultColumn: { minSize: 44, maxSize: 800, size: 150 },
  });

  return (
    <DataGrid
      table={table}
      // Clicking a template row is what loads its checklist below.
      onRowClick={(row) => onSelect(row.id)}
      recordCount={hasType ? rows.length : 0}
      isLoading={isLoading}
      error={error}
      onRetry={onRefresh}
      listingKey="projects.types.view::templates"
      tableLayout={{ width: 'fixed', columnsResizable: true, columnsVisibility: true }}
      emptyMessage={
        !hasType ? (
          <span className="block max-w-md">
            <span className="block text-sm font-semibold text-foreground">
              No project type selected
            </span>
            Select a project type to see its templates.
          </span>
        ) : (
          <span className="block max-w-md">
            <span className="block text-sm font-semibold text-foreground">
              This type has no templates
            </span>
            Without one, a project of this type has no roles to pick from and no
            checklist to start with.
          </span>
        )
      }
      // No emptyAction: the toolbar's Add is the one offer (#1335).
    >
      <Card>
        <CardHeader className="block pt-5">
          <CardTitle className="text-sm">Templates</CardTitle>
          <p className="mt-0.5 text-xs text-muted-foreground">
            A template decides the stakeholder roles offered and the checklist a new
            project copies in.
          </p>
          <DataGridListToolbar
            table={table}
            exportConfig={{ filename: 'project_templates_export.xlsx' }}
            onRefresh={onRefresh}
            isRefreshing={isFetching && !isLoading}
            primaryAction={
              <Button type="button" size="sm" disabled={!hasType} onClick={onAdd}>
                <Plus className="size-4" aria-hidden />
                Add template
              </Button>
            }
          />
        </CardHeader>
        <CardTable>
          <DataGridTable />
        </CardTable>
        <CardFooter>
          <DataGridPagination />
        </CardFooter>
      </Card>
    </DataGrid>
  );
}

/**
 * The master-detail tick.
 *
 * Master-detail needs the list to say which row the panel below is describing. A tick in
 * a fixed leading column does that without borrowing row SELECTION, which the toolbar
 * reads as "the user picked rows to act on" and would answer with a bulk-action strip.
 */
function selectedMarkerColumn<TRow extends { id: string }>(
  selectedId: string | null,
): ColumnDef<TRow> {
  return {
    id: 'selected',
    header: () => <span className="sr-only">Selected</span>,
    size: 44,
    enableSorting: false,
    enableHiding: false,
    enableResizing: false,
    cell: ({ row }) =>
      row.original.id === selectedId ? (
        <Check className="size-4 text-primary" aria-label="Selected" />
      ) : (
        <span className="sr-only">Not selected</span>
      ),
  };
}

function rowActionsColumn<TRow extends { id: string }>({
  editLabel,
  deleteLabel,
  onEdit,
  onDelete,
}: {
  editLabel: (row: TRow) => string;
  deleteLabel: (row: TRow) => string;
  onEdit: (row: TRow) => void;
  onDelete: (row: TRow) => void;
}): ColumnDef<TRow> {
  return {
    id: 'actions',
    header: () => <span className="sr-only">Actions</span>,
    size: 110,
    enableSorting: false,
    enableHiding: false,
    cell: ({ row }) => (
      <div className="flex items-center gap-1" onClick={(event) => event.stopPropagation()}>
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="icon"
              className="h-8 w-8"
              onClick={() => onEdit(row.original)}
              aria-label={editLabel(row.original)}
            >
              <Pencil className="size-4 text-muted-foreground" aria-hidden />
            </Button>
          </TooltipTrigger>
          <TooltipContent>Edit</TooltipContent>
        </Tooltip>
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="icon"
              className="h-8 w-8"
              onClick={() => onDelete(row.original)}
              aria-label={deleteLabel(row.original)}
            >
              <Trash2 className="size-4 text-destructive" aria-hidden />
            </Button>
          </TooltipTrigger>
          <TooltipContent>Delete</TooltipContent>
        </Tooltip>
      </div>
    ),
  };
}
