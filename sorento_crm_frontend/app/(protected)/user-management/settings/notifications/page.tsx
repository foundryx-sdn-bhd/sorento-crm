'use client';

import { useMemo } from 'react';
import { zodResolver } from '@hookform/resolvers/zod';
import { RiCheckboxCircleFill, RiErrorWarningFill } from '@remixicon/react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import type { ColumnDef } from '@tanstack/react-table';
import { useReactTable, getCoreRowModel } from '@tanstack/react-table';
import {
  AppWindowMac,
  Bell,
  LoaderCircleIcon,
  MailWarning,
  UserPlus,
  Users,
} from 'lucide-react';
import { useForm } from 'react-hook-form';
import { toast } from '@/lib/toast';
import { apiFetch } from '@/lib/api';
import { Alert, AlertIcon, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Card,
  CardContent,
  CardFooter,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import { Checkbox } from '@/components/ui/checkbox';
import { Form, FormControl, FormField, FormItem } from '@/components/ui/form';
import { SearchableMultiSelect } from '@/components/common/SearchableMultiSelect';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { useHasPermission } from '@/hooks/usePermissions';
import { useSettings } from '../components/settings-context';
import {
  NotificationSettingsSchema,
  NotificationSettingsSchemaType,
} from '../forms/notification-settings-schema';

const notificationSettings = [
  {
    label: 'Stock Alerts',
    description: 'Notify when stock reaches the threshold.',
    emailField: 'notifyStockEmail',
    webField: 'notifyStockWeb',
    roleIdsField: 'notifyStockRoleIds',
  },
  {
    label: 'New Delivery Orders',
    description: 'Notify when new delivery orders are received.',
    emailField: 'notifyNewOrderEmail',
    webField: 'notifyNewOrderWeb',
    roleIdsField: 'notifyNewOrderRoleIds',
  },
  {
    label: 'Delivery Order Status Updates',
    description: 'Notify when a delivery order status is updated.',
    emailField: 'notifyOrderStatusUpdateEmail',
    webField: 'notifyOrderStatusUpdateWeb',
    roleIdsField: 'notifyOrderStatusUpdateRoleIds',
  },
  {
    label: 'Payment Failures',
    description: 'Notify when a payment failure occurs.',
    emailField: 'notifyPaymentFailureEmail',
    webField: 'notifyPaymentFailureWeb',
    roleIdsField: 'notifyPaymentFailureRoleIds',
  },
  {
    label: 'System Errors',
    description: 'Notify when system errors occur.',
    emailField: 'notifySystemErrorFailureEmail',
    webField: 'notifySystemErrorWeb',
    roleIdsField: 'notifySystemErrorRoleIds',
  },
] as const;

type NotificationSettingRow = (typeof notificationSettings)[number];

/**
 * The grid's rows, built ONCE. `data={[...notificationSettings]}` inline handed
 * TanStack a new array identity on every render, and TanStack reads `data` by
 * identity: with `autoResetPageIndex` on it reset the page, which set state,
 * which re-rendered, which built another array. The tab pegged a core on load,
 * with a clean console (M5 run 2 evidence, finding 1) - measured at ~400 renders
 * and ~6,000 DOM mutations a second with no interaction at all, which is why the
 * checkbox looked like the culprit. This list never changes, so it is built once
 * and the loop has nothing to feed on.
 */
const NOTIFICATION_ROWS: NotificationSettingRow[] = [...notificationSettings];

const NotificationSettingsPage = () => {
  const queryClient = useQueryClient();
  const { settings, roles } = useSettings();
  // The save needs the edit slug (SETTINGS-PERM-GATE); without it the tab is read-only.
  const canEdit = useHasPermission('user_management.settings.edit');

  const form = useForm<NotificationSettingsSchemaType>({
    resolver: zodResolver(NotificationSettingsSchema),
    mode: 'onTouched',
    defaultValues: notificationSettings.reduce<
      Partial<NotificationSettingsSchemaType>
    >(
      (defaults, { emailField, webField, roleIdsField }) => ({
        ...defaults,
        [emailField]:
          (settings as NotificationSettingsSchemaType)[
            emailField as keyof NotificationSettingsSchemaType
          ] ?? false,
        [webField]:
          (settings as NotificationSettingsSchemaType)[
            webField as keyof NotificationSettingsSchemaType
          ] ?? false,
        [roleIdsField]:
          (settings as NotificationSettingsSchemaType)[
            roleIdsField as keyof NotificationSettingsSchemaType
          ] ?? [],
      }),
      {},
    ),
  });

  const mutation = useMutation({
    mutationFn: async (values: NotificationSettingsSchemaType) => {
      const response = await apiFetch(
        '/api/user-management/settings/notifications',
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(values),
        },
      );

      if (!response.ok) {
        const { message } = await response.json();
        throw new Error(message);
      }

      return response.json();
    },
    onSuccess: () => {
      toast.custom(
        () => (
          <Alert variant="mono" icon="success">
            <AlertIcon>
              <RiCheckboxCircleFill />
            </AlertIcon>
            <AlertTitle>Settings updated successfully</AlertTitle>
          </Alert>
        ),
        {
          position: 'top-center',
        },
      );

      queryClient.invalidateQueries({ queryKey: ['system-settings'] });
    },
    onError: (error: Error) => {
      toast.custom(
        () => (
          <Alert variant="mono" icon="destructive">
            <AlertIcon>
              <RiErrorWarningFill />
            </AlertIcon>
            <AlertTitle>{error.message}</AlertTitle>
          </Alert>
        ),
        {
          position: 'top-center',
        },
      );
    },
  });

  const handleSubmit = (values: NotificationSettingsSchemaType) => {
    mutation.mutate(values);
  };

  const handleReset = () => {
    form.reset();
  };


  const isProcessing = mutation.status === 'pending';

  const columns = useMemo<ColumnDef<NotificationSettingRow>[]>(
    () => [
      {
        accessorKey: 'label',
        header: ({ column }) => (
          <DataGridColumnHeader title="Notification" icon={<Bell />} column={column} />
        ),
        cell: ({ row }) => (
          <div className="space-y-1">
            <div className="text-md font-semibold">{row.original.label}</div>
            <div className="text-muted-foreground font-2sm font-regular">
              {row.original.description}
            </div>
          </div>
        ),
        size: 400,
        meta: { headerTitle: 'Notification' },
      },
      {
        id: 'roles',
        header: ({ column }) => (
          <DataGridColumnHeader title="Users" icon={<Users />} column={column} />
        ),
        cell: ({ row }) => {
          const { roleIdsField } = row.original;
          const selectedRoleIds =
            (form.watch(roleIdsField as keyof NotificationSettingsSchemaType) as string[]) ?? [];
          return (
            <div className="flex items-center gap-3">
              <SearchableMultiSelect
                value={selectedRoleIds}
                onChange={(next) =>
                  form.setValue(roleIdsField as keyof NotificationSettingsSchemaType, next, {
                    shouldDirty: true,
                  })
                }
                emptyMessage="No roles found."
                className="w-[200px]"
                // Picked roles render as badges beside this control, so the trigger
                // stays the compact icon button it has always been.
                renderTrigger={() => (
                  <Button
                    variant="outline"
                    mode="icon"
                    className="h-7! w-7!"
                    data-testid="notify-roles-trigger"
                    aria-label="Add notify roles"
                  >
                    <UserPlus className="size-3.5!" />
                  </Button>
                )}
                options={(roles ?? []).map((role) => ({
                  value: role.id,
                  label: role.name,
                }))}
              />
              <div className="flex items-center flex-wrap gap-2">
                {selectedRoleIds.length > 0 ? (
                  selectedRoleIds.map((roleId) => {
                    const role = roles.find((r) => r.id === roleId);
                    return (
                      <Badge key={roleId} variant="secondary">
                        {role?.name}
                      </Badge>
                    );
                  })
                ) : (
                  <span className="text-muted-foreground">Not set</span>
                )}
              </div>
            </div>
          );
        },
        size: 320,
        meta: { headerTitle: 'Users' },
      },
      {
        id: 'email',
        header: ({ column }) => (
          <DataGridColumnHeader
            title="Email"
            icon={<MailWarning />}
            column={column}
            className="justify-center"
          />
        ),
        cell: ({ row }) => (
          <div className="flex justify-center">
            <FormField
              control={form.control}
              name={row.original.emailField as keyof NotificationSettingsSchemaType}
              render={({ field }) => (
                <FormItem className="items-center">
                  <FormControl>
                    <Checkbox checked={Boolean(field.value)} onCheckedChange={field.onChange} />
                  </FormControl>
                </FormItem>
              )}
            />
          </div>
        ),
        size: 140,
        meta: { headerTitle: 'Email' },
      },
      {
        id: 'web',
        header: ({ column }) => (
          <DataGridColumnHeader
            title="Web"
            icon={<AppWindowMac />}
            column={column}
            className="justify-center"
          />
        ),
        cell: ({ row }) => (
          <div className="flex justify-center">
            <FormField
              control={form.control}
              name={row.original.webField as keyof NotificationSettingsSchemaType}
              render={({ field }) => (
                <FormItem className="items-center">
                  <FormControl>
                    <Checkbox checked={Boolean(field.value)} onCheckedChange={field.onChange} />
                  </FormControl>
                </FormItem>
              )}
            />
          </div>
        ),
        size: 140,
        enableResizing: false,
        meta: { headerTitle: 'Web' },
      },
    ],
    [form, roles],
  );

  const table = useReactTable({
    columns,
    data: NOTIFICATION_ROWS,
    getRowId: (row) => row.label,
    getCoreRowModel: getCoreRowModel(),
    columnResizeMode: 'onChange',
  });

  return (
    <form onSubmit={form.handleSubmit(handleSubmit)}>
      <Form {...form}>
        <Card>
          <CardHeader className="border-b border-border">
            <CardTitle>Notification Settings</CardTitle>
          </CardHeader>
          <CardContent className="px-0 py-2.5">
            <fieldset disabled={!canEdit} className="min-w-0">
              <DataGrid
                table={table}
                recordCount={notificationSettings.length}
                tableLayout={{ width: 'fixed', columnsResizable: true }}
              >
                <DataGridTable />
              </DataGrid>
            </fieldset>
          </CardContent>
          {canEdit && (
            <CardFooter className="flex justify-end gap-4 py-5 px-10">
              <Button type="button" variant="outline" onClick={handleReset}>
                Reset
              </Button>
              <Button type="submit" disabled={isProcessing}>
                {isProcessing && <LoaderCircleIcon className="animate-spin" />}
                Save Settings
              </Button>
            </CardFooter>
          )}
        </Card>
      </Form>
    </form>
  );
};

export default NotificationSettingsPage;
