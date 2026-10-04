'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  getCoreRowModel,
  useReactTable,
  type ColumnDef,
  type RowSelectionState,
} from '@tanstack/react-table';
import { Pencil, Plus, Star, Trash2 } from 'lucide-react';
import { toast } from '@/lib/toast';
import { Button } from '@/components/ui/button';
import { Card, CardHeader, CardTable } from '@/components/ui/card';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Checkbox } from '@/components/ui/checkbox';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridListToolbar } from '@/components/ui/data-grid-list-toolbar';
import { buildSelectColumn } from '@/components/ui/data-grid-select-column';
import { DataGridTable } from '@/components/ui/data-grid-table';
import {
  SearchableSelect,
  type SearchableSelectOption,
} from '@/components/common/SearchableSelect';
import {
  createRespondWorkspace,
  deleteRespondWorkspace,
  listIdeationProducts,
  listRespondWorkspaces,
  setDefaultRespondWorkspace,
  updateRespondWorkspace,
  updateRespondWorkspaceChatbotRetry,
  type RespondWorkspace,
  type RespondWorkspaceChatbotRetryBody,
  type RespondWorkspaceCreateBody,
  type RespondWorkspaceUpdateBody,
} from '../services/respondWorkspaceService';

interface FormState {
  space_id: string;
  name: string;
  base_url: string;
  whatsapp_number: string;
  api_key: string;
  ideation_shared_service_url: string;
  ideation_product_id: string;
  ideation_intake_api_key: string;
  ideation_embed_connection_id: string;
  ideation_embed_fe_base_url: string;
  ideation_embed_signing_secret: string;
  chatbot_retry_ingress_url: string;
  chatbot_retry_ingress_key: string;
  is_active: boolean;
  is_default: boolean;
}

const EMPTY_FORM: FormState = {
  space_id: '',
  name: '',
  base_url: '',
  whatsapp_number: '',
  api_key: '',
  ideation_shared_service_url: '',
  ideation_product_id: '',
  ideation_intake_api_key: '',
  ideation_embed_connection_id: '',
  ideation_embed_fe_base_url: '',
  ideation_embed_signing_secret: '',
  chatbot_retry_ingress_url: '',
  chatbot_retry_ingress_key: '',
  is_active: true,
  is_default: false,
};

export default function RespondWorkspacesAdmin() {
  const queryClient = useQueryClient();
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<RespondWorkspace | null>(null);
  const [deleteId, setDeleteId] = useState<string | null>(null);
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  // The stored retry key is never sent to the browser, so the input is always blank and a
  // blank input cannot mean "remove it". This flag is the explicit ask, and it is what
  // makes the key sent as `''` on save.
  const [clearRetryKey, setClearRetryKey] = useState(false);
  const [rowSelection, setRowSelection] = useState<RowSelectionState>({});
  // Ideation-product dropdown state: fetched name cache (to resolve the stored id
  // to a human name) + the last upstream error surfaced under the select.
  const [ideationProductNames, setIdeationProductNames] = useState<
    Record<string, string>
  >({});
  const [ideationProductError, setIdeationProductError] = useState<string | null>(null);

  // The dropdown can query shared-service when we have a URL+key to preview live,
  // OR when editing an existing workspace (the proxy falls back to its saved
  // URL + decrypted key via workspace_id).
  const hasLiveIdeationPair = Boolean(
    form.ideation_shared_service_url.trim() && form.ideation_intake_api_key.trim(),
  );
  const canFetchIdeationProducts = hasLiveIdeationPair || Boolean(editing);

  // Fetched product options for the (static-filter) SearchableSelect. Loaded once
  // when the dialog opens; the proxy returns the whole list, the select filters it.
  const [ideationProductOptions, setIdeationProductOptions] = useState<
    SearchableSelectOption[]
  >([]);

  const loadIdeationProducts = useCallback(async () => {
    const result = await listIdeationProducts({
      workspaceId: editing?.id ?? null,
      baseUrl: form.ideation_shared_service_url.trim() || null,
      apiKey: form.ideation_intake_api_key.trim() || null,
    });
    setIdeationProductError(result.error);
    setIdeationProductNames((prev) => {
      const next = { ...prev };
      for (const p of result.products) next[p.id] = p.name;
      return next;
    });
    setIdeationProductOptions(
      result.products.map((p) => ({ value: p.id, label: p.name })),
    );
  }, [editing?.id, form.ideation_shared_service_url, form.ideation_intake_api_key]);

  // Fetch when the dialog is open AND we can reach shared-service (a live URL+key
  // pair, or an existing workspace whose saved creds the proxy falls back to).
  useEffect(() => {
    if (dialogOpen && canFetchIdeationProducts) void loadIdeationProducts();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dialogOpen, canFetchIdeationProducts, editing?.id]);

  // Options: a clear entry + fetched products + the stored id (so the trigger
  // shows a name even before a fetch resolves) - never a raw bare UUID.
  const ideationProductSelectOptions: SearchableSelectOption[] = useMemo(() => {
    const opts: SearchableSelectOption[] = [
      { value: '', label: ' - None - ' },
      ...ideationProductOptions,
    ];
    const sel = form.ideation_product_id;
    if (sel && !opts.some((o) => o.value === sel)) {
      opts.splice(1, 0, {
        value: sel,
        label: ideationProductNames[sel] ?? 'Selected product (name unavailable)',
      });
    }
    return opts;
  }, [ideationProductOptions, ideationProductNames, form.ideation_product_id]);

  const {
    data: workspaces = [],
    isLoading,
    error,
    refetch,
  } = useQuery({
    queryKey: ['respond-workspaces'],
    queryFn: listRespondWorkspaces,
  });

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['respond-workspaces'] });
    queryClient.invalidateQueries({ queryKey: ['respond-workspace-select'] });
  };

  const createMutation = useMutation({
    mutationFn: createRespondWorkspace,
    onSuccess: () => {
      invalidate();
      toast.success('Workspace created');
      setDialogOpen(false);
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const updateMutation = useMutation({
    // Two calls, because the two chatbot retry fields live behind their own route and
    // their own permission - and each is sent only when something it carries changed.
    //
    // That INDEPENDENCE is the point (security review round 2). AC-804 gave the retry
    // pair a narrow route so a principal holding `user_management.settings.edit` alone
    // can set it without also being handed `api_key`, `base_url`, `space_id` and
    // `is_default`. Sending the row PUT unconditionally handed that principal a 403 on
    // the way in and they never reached the narrow route at all, which is the whole
    // capability AC-804 was written to give them. The retry call goes first for the same
    // reason: it is the one this operator is entitled to make.
    //
    // A refusal on either still leaves the operator in the dialog with what they typed.
    mutationFn: async ({
      id,
      body,
      retry,
    }: {
      id: string;
      body: RespondWorkspaceUpdateBody | null;
      retry: RespondWorkspaceChatbotRetryBody | null;
    }) => {
      if (retry) await updateRespondWorkspaceChatbotRetry(id, retry);
      if (body) await updateRespondWorkspace(id, body);
    },
    onSuccess: () => {
      invalidate();
      toast.success('Workspace updated');
      setDialogOpen(false);
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const deleteMutation = useMutation({
    mutationFn: deleteRespondWorkspace,
    onSuccess: () => {
      invalidate();
      toast.success('Workspace deleted');
      setDeleteId(null);
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const setDefaultMutation = useMutation({
    mutationFn: setDefaultRespondWorkspace,
    onSuccess: () => {
      invalidate();
      toast.success('Default workspace updated');
    },
    onError: (e: Error) => toast.error(e.message),
  });

  function openCreate() {
    setEditing(null);
    setForm(EMPTY_FORM);
    setClearRetryKey(false);
    setIdeationProductError(null);
    setDialogOpen(true);
  }

  function openEdit(row: RespondWorkspace) {
    setEditing(row);
    setIdeationProductError(null);
    setClearRetryKey(false);
    setForm({
      space_id: row.space_id,
      name: row.name ?? '',
      base_url: row.base_url ?? '',
      whatsapp_number: row.whatsapp_number ?? '',
      api_key: '',
      ideation_shared_service_url: row.ideation_shared_service_url ?? '',
      ideation_product_id: row.ideation_product_id ?? '',
      ideation_intake_api_key: '',
      ideation_embed_connection_id: row.ideation_embed_connection_id ?? '',
      ideation_embed_fe_base_url: row.ideation_embed_fe_base_url ?? '',
      ideation_embed_signing_secret: '',
      chatbot_retry_ingress_url: row.chatbot_retry_ingress_url ?? '',
      // Write-only: the stored key is never sent to the browser, so the input starts
      // empty on every open and a blank save keeps whatever is on the row.
      chatbot_retry_ingress_key: '',
      is_active: row.is_active,
      is_default: row.is_default,
    });
    setDialogOpen(true);
  }

  function save() {
    if (editing) {
      const body: RespondWorkspaceUpdateBody = {
        space_id: form.space_id.trim(),
        name: form.name.trim() || null,
        base_url: form.base_url.trim() || null,
        whatsapp_number: form.whatsapp_number.trim() || null,
        is_active: form.is_active,
        is_default: form.is_default,
        ideation_shared_service_url: form.ideation_shared_service_url.trim() || null,
        ideation_product_id: form.ideation_product_id.trim() || null,
        ideation_embed_connection_id: form.ideation_embed_connection_id.trim() || null,
        ideation_embed_fe_base_url: form.ideation_embed_fe_base_url.trim() || null,
      };
      if (form.api_key.trim()) body.api_key = form.api_key.trim();
      if (form.ideation_intake_api_key.trim())
        body.ideation_intake_api_key = form.ideation_intake_api_key.trim();
      if (form.ideation_embed_signing_secret.trim())
        body.ideation_embed_signing_secret = form.ideation_embed_signing_secret.trim();
      // ALWAYS the trimmed string, never null: `''` is what clears the stored URL, and
      // the field's own copy says leaving it blank turns Retry off.
      const retry: RespondWorkspaceChatbotRetryBody = {
        chatbot_retry_ingress_url: form.chatbot_retry_ingress_url.trim(),
      };
      // The key is only MENTIONED when there is something to say about it: a new value,
      // or the explicit ask to remove the stored one. An untouched blank input leaves it
      // alone, which is the only reading a write-only field can have.
      if (form.chatbot_retry_ingress_key.trim())
        retry.chatbot_retry_ingress_key = form.chatbot_retry_ingress_key.trim();
      else if (clearRetryKey) retry.chatbot_retry_ingress_key = '';

      // ONLY the row PUT is skippable, and only because a 403 on it used to block the
      // narrow retry route a `user_management.settings.edit` principal is entitled to
      // (security review round 2). Which of its fields count as changed is derived from
      // the body's OWN keys rather than a hand-kept list, so a field added above joins
      // the comparison with nothing to remember: a key the row does not carry is one of
      // the three write-only secrets, present only when typed, and a key the row does
      // carry is compared with null and '' read as the same absence.
      const current = editing as unknown as Record<string, unknown>;
      const rowChanged = (Object.keys(body) as (keyof RespondWorkspaceUpdateBody)[]).some(
        (key) =>
          current[key] === undefined
            ? true
            : (body[key] ?? null) !== (current[key] ?? null),
      );

      // The RETRY call is always sent, and that is deliberate rather than lazy. Skipping
      // it needs the draft to be compared against `editing`, which is a SNAPSHOT taken
      // when the dialog opened: after any save that did not refresh it, or a list refetch
      // that has not landed, a genuine edit compares equal to a stale row and the save is
      // dropped with the dialog cheerfully reporting success. Blanking the URL is exactly
      // where that costs the most, because "Retry is still on" is the state the operator
      // was trying to leave. One idempotent PUT on the low-privilege route is the cheaper
      // side of that trade (browser evidence, 6 Sep 2026).
      updateMutation.mutate({
        id: editing.id,
        body: rowChanged ? body : null,
        retry,
      });
    } else {
      if (!form.space_id.trim() || !form.api_key.trim()) {
        toast.error('Space ID and API key are required');
        return;
      }
      const body: RespondWorkspaceCreateBody = {
        space_id: form.space_id.trim(),
        name: form.name.trim() || null,
        base_url: form.base_url.trim() || null,
        whatsapp_number: form.whatsapp_number.trim() || null,
        is_active: form.is_active,
        is_default: form.is_default,
        api_key: form.api_key.trim(),
        ideation_shared_service_url: form.ideation_shared_service_url.trim() || null,
        ideation_product_id: form.ideation_product_id.trim() || null,
        ideation_intake_api_key: form.ideation_intake_api_key.trim() || null,
        ideation_embed_connection_id: form.ideation_embed_connection_id.trim() || null,
        ideation_embed_fe_base_url: form.ideation_embed_fe_base_url.trim() || null,
        ideation_embed_signing_secret: form.ideation_embed_signing_secret.trim() || null,
        chatbot_retry_ingress_url: form.chatbot_retry_ingress_url.trim() || null,
        chatbot_retry_ingress_key: form.chatbot_retry_ingress_key.trim() || null,
      };
      createMutation.mutate(body);
    }
  }

  const columns = useMemo<ColumnDef<RespondWorkspace>[]>(
    () => [
      buildSelectColumn<RespondWorkspace>(),
      {
        id: 'name',
        accessorFn: (row) => row.name ?? '',
        header: ({ column }) => <DataGridColumnHeader title="Name" column={column} />,
        size: 220,
        enableSorting: false,
        meta: { headerTitle: 'Name', skeleton: <Skeleton className="h-4 w-32" /> },
        cell: ({ row }) => (
          <span className="font-medium">{row.original.name || '-'}</span>
        ),
      },
      {
        id: 'space_id',
        accessorFn: (row) => row.space_id,
        header: ({ column }) => <DataGridColumnHeader title="Space ID" column={column} />,
        size: 160,
        enableSorting: false,
        meta: { headerTitle: 'Space ID', skeleton: <Skeleton className="h-4 w-24" /> },
        cell: ({ row }) => <span className="font-mono">{row.original.space_id}</span>,
      },
      {
        id: 'base_url',
        accessorFn: (row) => row.base_url ?? '',
        header: ({ column }) => <DataGridColumnHeader title="Base URL" column={column} />,
        size: 240,
        enableSorting: false,
        meta: { headerTitle: 'Base URL', skeleton: <Skeleton className="h-4 w-40" /> },
        cell: ({ row }) => (
          <span className="truncate" title={row.original.base_url ?? ''}>
            {row.original.base_url || '-'}
          </span>
        ),
      },
      {
        id: 'whatsapp_number',
        accessorFn: (row) => row.whatsapp_number ?? '',
        header: ({ column }) => <DataGridColumnHeader title="WhatsApp" column={column} />,
        size: 140,
        enableSorting: false,
        meta: { headerTitle: 'WhatsApp', skeleton: <Skeleton className="h-4 w-24" /> },
        cell: ({ row }) => (
          <span className="font-mono truncate" title={row.original.whatsapp_number ?? ''}>
            {row.original.whatsapp_number || '-'}
          </span>
        ),
      },
      {
        id: 'api_key_masked',
        accessorFn: (row) => row.api_key_masked ?? '',
        header: ({ column }) => <DataGridColumnHeader title="API Key" column={column} />,
        size: 140,
        enableSorting: false,
        meta: { headerTitle: 'API Key', skeleton: <Skeleton className="h-4 w-20" /> },
        cell: ({ row }) => <span className="font-mono">{row.original.api_key_masked ?? '-'}</span>,
      },
      {
        id: 'is_active',
        accessorFn: (row) => row.is_active,
        header: ({ column }) => <DataGridColumnHeader title="Status" column={column} />,
        size: 110,
        enableSorting: false,
        meta: { headerTitle: 'Status', skeleton: <Skeleton className="h-6 w-16" /> },
        cell: ({ row }) =>
          row.original.is_active ? (
            <Badge variant="primary">Active</Badge>
          ) : (
            <Badge variant="secondary">Inactive</Badge>
          ),
      },
      {
        id: 'is_default',
        accessorFn: (row) => row.is_default,
        header: ({ column }) => <DataGridColumnHeader title="Default" column={column} />,
        size: 130,
        enableSorting: false,
        meta: { headerTitle: 'Default', skeleton: <Skeleton className="h-6 w-16" /> },
        cell: ({ row }) =>
          row.original.is_default ? (
            <Badge variant="primary" className="gap-1">
              <Star className="size-3 fill-current" />
              Default
            </Badge>
          ) : (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setDefaultMutation.mutate(row.original.id)}
              disabled={setDefaultMutation.isPending}
            >
              Set default
            </Button>
          ),
      },
      {
        id: 'actions',
        header: '',
        size: 120,
        enableSorting: false,
        enableHiding: false,
        enableResizing: false,
        cell: ({ row }) => (
          <div className="flex gap-1">
            <Button variant="ghost" size="icon" onClick={() => openEdit(row.original)} aria-label="Edit">
              <Pencil className="size-4" />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              onClick={() => setDeleteId(row.original.id)}
              aria-label="Delete"
            >
              <Trash2 className="size-4 text-destructive" />
            </Button>
          </div>
        ),
      },
    ],
    [setDefaultMutation],
  );

  const table = useReactTable({
    columns,
    data: workspaces,
    getRowId: (row) => row.id,
    state: { pagination: { pageIndex: 0, pageSize: 25 }, rowSelection },
    enableRowSelection: true,
    onRowSelectionChange: setRowSelection,
    getCoreRowModel: getCoreRowModel(),
    columnResizeMode: 'onChange',
    enableColumnResizing: true,
  });

  // The one offer this listing makes, in both places it belongs: the
  // toolbar, and the empty state's next step (S5-06).
  const listPrimaryAction = (
    <Button onClick={openCreate}>
      <Plus className="size-4 mr-2" />
      Add workspace
    </Button>
  );

  return (
    <div className="space-y-6">
      <Card>
        <DataGrid
          table={table}
          recordCount={workspaces.length}
          isLoading={isLoading}
          error={error}
          onRetry={() => void refetch()}
          emptyMessage="No Respond.io workspaces configured. Add one to start syncing contacts."
          tableLayout={{ width: 'fixed', columnsResizable: true, columnsVisibility: true }}
        >
          <CardHeader className="block">
            <DataGridListToolbar
              table={table}
              exportConfig={{ filename: 'respond_workspaces_export.xlsx' }}
              primaryAction={listPrimaryAction}
            />
          </CardHeader>
          <CardTable>
            <DataGridTable />
          </CardTable>
        </DataGrid>
      </Card>

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="sm:max-w-[520px]">
          <DialogHeader>
            <DialogTitle>{editing ? 'Edit workspace' : 'Add workspace'}</DialogTitle>
          </DialogHeader>
          <div className="grid gap-4 py-2">
            <div className="grid gap-2">
              <Label htmlFor="ws-name">Name</Label>
              <Input
                id="ws-name"
                value={form.name}
                onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                placeholder="e.g. Sorento Main"
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="ws-space">Space ID</Label>
              <Input
                id="ws-space"
                value={form.space_id}
                onChange={(e) => setForm((f) => ({ ...f, space_id: e.target.value }))}
                placeholder="e.g. 364817"
              />
              <p className="text-xs text-muted-foreground">
                Respond.io external workspace identifier (used by MCP / sync routing).
              </p>
            </div>
            <div className="grid gap-2">
              <Label htmlFor="ws-api">API Key {editing && <span className="text-muted-foreground">(leave blank to keep current)</span>}</Label>
              <Input
                id="ws-api"
                type="password"
                value={form.api_key}
                onChange={(e) => setForm((f) => ({ ...f, api_key: e.target.value }))}
                placeholder={editing ? '•••• (unchanged)' : 'Paste API key from Respond.io'}
                autoComplete="new-password"
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="ws-base">Base URL</Label>
              <Input
                id="ws-base"
                value={form.base_url}
                onChange={(e) => setForm((f) => ({ ...f, base_url: e.target.value }))}
                placeholder="https://api.respond.io"
              />
              <p className="text-xs text-muted-foreground">
                Optional override. Defaults to the global Respond.io base URL when blank.
              </p>
            </div>
            <div className="grid gap-2">
              <Label htmlFor="ws-wa">WhatsApp Number</Label>
              <Input
                id="ws-wa"
                value={form.whatsapp_number}
                onChange={(e) => setForm((f) => ({ ...f, whatsapp_number: e.target.value }))}
                placeholder="e.g. 60123456789"
                inputMode="numeric"
              />
              <p className="text-xs text-muted-foreground">
                Business WhatsApp number for this workspace&apos;s channel (digits only,
                country code included). Shown on the customer portal as the
                &quot;Message us on WhatsApp&quot; button when a verification code cannot be
                delivered. Leave blank to hide that button.
              </p>
            </div>
            <div className="border-t pt-4 mt-1">
              <p className="text-sm font-medium">Ideation connection</p>
              <p className="text-xs text-muted-foreground">
                Connects this workspace to the shared idea-intake service. Configure all
                three on the default workspace to enable idea capture; leave blank to keep
                it off.
              </p>
            </div>
            <div className="grid gap-2">
              <Label htmlFor="ws-ideation-url">Shared-service API URL</Label>
              <Input
                id="ws-ideation-url"
                value={form.ideation_shared_service_url}
                onChange={(e) =>
                  setForm((f) => ({ ...f, ideation_shared_service_url: e.target.value }))
                }
                placeholder="http://localhost:8001"
              />
              <p className="text-xs text-muted-foreground">
                The shared-service <strong>backend / API</strong> base URL (e.g.{' '}
                <code>http://localhost:8001</code>), NOT its app/frontend URL. The
                idea-intake endpoints live here.
              </p>
            </div>
            <div className="grid gap-2">
              <Label htmlFor="ws-ideation-key">
                Intake API key{' '}
                {editing && (
                  <span className="text-muted-foreground">(leave blank to keep current)</span>
                )}
              </Label>
              <Input
                id="ws-ideation-key"
                type="password"
                value={form.ideation_intake_api_key}
                onChange={(e) =>
                  setForm((f) => ({ ...f, ideation_intake_api_key: e.target.value }))
                }
                placeholder={
                  editing
                    ? form.ideation_intake_api_key
                      ? ''
                      : (editing.ideation_intake_api_key_masked ?? '•••• (unchanged)')
                    : 'Bearer token for the create_idea intake endpoint'
                }
                autoComplete="new-password"
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="ws-ideation-product">Ideation product</Label>
              <SearchableSelect
                value={form.ideation_product_id}
                onChange={(v) => setForm((f) => ({ ...f, ideation_product_id: v }))}
                options={ideationProductSelectOptions}
                disabled={!canFetchIdeationProducts}
                placeholder={
                  canFetchIdeationProducts
                    ? 'Select a product…'
                    : 'Enter the Shared-service URL + Intake API key first'
                }
                emptyMessage="No software products found for this key."
              />
              {ideationProductError && canFetchIdeationProducts ? (
                <p className="text-xs text-destructive">{ideationProductError}</p>
              ) : (
                <p className="text-xs text-muted-foreground">
                  The shared-service Product this workspace&apos;s ideas are filed under.
                  Fetched live from the shared-service by name.
                </p>
              )}
            </div>
            <div className="border-t pt-4 mt-1">
              <p className="text-sm font-medium">Ideas embed (iframe SSO)</p>
              <p className="text-xs text-muted-foreground">
                Lets a logged-in user open the shared Ideas board inside the app with no
                second login. Set all three on the default workspace to enable it; leave
                blank to keep it off.
              </p>
            </div>
            <div className="grid gap-2">
              <Label htmlFor="ws-embed-conn">Embed connection ID</Label>
              <Input
                id="ws-embed-conn"
                value={form.ideation_embed_connection_id}
                onChange={(e) =>
                  setForm((f) => ({ ...f, ideation_embed_connection_id: e.target.value }))
                }
                placeholder="Connection id from the shared-service embed registry"
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="ws-embed-secret">
                Embed signing secret{' '}
                {editing && (
                  <span className="text-muted-foreground">(leave blank to keep current)</span>
                )}
              </Label>
              <Input
                id="ws-embed-secret"
                type="password"
                value={form.ideation_embed_signing_secret}
                onChange={(e) =>
                  setForm((f) => ({ ...f, ideation_embed_signing_secret: e.target.value }))
                }
                placeholder={
                  editing
                    ? form.ideation_embed_signing_secret
                      ? ''
                      : (editing.ideation_embed_signing_secret_masked ?? '•••• (unchanged)')
                    : 'Shared secret that signs the SSO assertion'
                }
                autoComplete="new-password"
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="ws-embed-fe">Embed FE base URL</Label>
              <Input
                id="ws-embed-fe"
                value={form.ideation_embed_fe_base_url}
                onChange={(e) =>
                  setForm((f) => ({ ...f, ideation_embed_fe_base_url: e.target.value }))
                }
                placeholder="https://chat.foundryx.my"
              />
              <p className="text-xs text-muted-foreground">
                The shared-service <strong>frontend</strong> root the iframe points at
                (e.g. <code>https://chat.foundryx.my</code>), NOT the backend / API URL
                above. These are different values.
              </p>
            </div>
            <div className="border-t pt-4 mt-1">
              <p className="text-sm font-medium">Chatbot retry</p>
              <p className="text-xs text-muted-foreground">
                Where a failed chatbot turn is re-sent from Chat History. Leave blank to
                turn Retry off for this workspace.
              </p>
            </div>
            <div className="grid gap-2">
              <Label htmlFor="ws-retry-url">Chatbot retry webhook URL</Label>
              <Input
                id="ws-retry-url"
                value={form.chatbot_retry_ingress_url}
                onChange={(e) =>
                  setForm((f) => ({ ...f, chatbot_retry_ingress_url: e.target.value }))
                }
                placeholder="https://…/webhook/…"
              />
              <p className="text-xs text-muted-foreground">
                Must be https, and must not point back at this system or at a private
                address.
              </p>
            </div>
            <div className="grid gap-2">
              <Label htmlFor="ws-retry-key">
                Retry key{' '}
                {editing && editing.has_chatbot_retry_key && !clearRetryKey && (
                  <span className="text-muted-foreground">
                    (Key set - leave blank to keep current)
                  </span>
                )}
              </Label>
              <Input
                id="ws-retry-key"
                type="password"
                value={form.chatbot_retry_ingress_key}
                onChange={(e) => {
                  setClearRetryKey(false);
                  setForm((f) => ({ ...f, chatbot_retry_ingress_key: e.target.value }));
                }}
                placeholder={
                  editing && editing.has_chatbot_retry_key && !clearRetryKey
                    ? '•••• (unchanged)'
                    : 'Sent with each retry so the receiving end can check it'
                }
                autoComplete="new-password"
              />
              {editing && editing.has_chatbot_retry_key && !clearRetryKey && (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="justify-self-start"
                  onClick={() => {
                    setClearRetryKey(true);
                    setForm((f) => ({ ...f, chatbot_retry_ingress_key: '' }));
                  }}
                >
                  Remove stored key
                </Button>
              )}
              {clearRetryKey && (
                <p className="text-xs text-destructive">
                  The stored key will be removed when you save.{' '}
                  <button
                    type="button"
                    className="underline"
                    onClick={() => setClearRetryKey(false)}
                  >
                    Keep it
                  </button>
                </p>
              )}
            </div>
            <div className="flex items-center gap-2">
              <Checkbox
                id="ws-active"
                checked={form.is_active}
                onCheckedChange={(v) => setForm((f) => ({ ...f, is_active: v === true }))}
              />
              <Label htmlFor="ws-active">Active</Label>
            </div>
            <div className="flex items-center gap-2">
              <Checkbox
                id="ws-default"
                checked={form.is_default}
                onCheckedChange={(v) => setForm((f) => ({ ...f, is_default: v === true }))}
              />
              <Label htmlFor="ws-default">
                Set as default - new contacts from sync land here when no workspace is set.
              </Label>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDialogOpen(false)}>
              Cancel
            </Button>
            <Button
              onClick={save}
              disabled={
                createMutation.isPending ||
                updateMutation.isPending ||
                !form.space_id.trim() ||
                (!editing && !form.api_key.trim())
              }
            >
              {editing ? 'Update' : 'Create'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={!!deleteId} onOpenChange={(open) => !open && setDeleteId(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete workspace</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">
            Delete this Respond.io workspace? Contacts referencing it will have
            their workspace cleared (set to none). This action cannot be undone.
          </p>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteId(null)}>
              Cancel
            </Button>
            <Button
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              onClick={() => deleteId && deleteMutation.mutate(deleteId)}
              disabled={deleteMutation.isPending}
            >
              Delete
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
