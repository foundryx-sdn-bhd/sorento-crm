'use client';

import { useEffect, useState } from 'react';
import { zodResolver } from '@hookform/resolvers/zod';
import { RiCheckboxCircleFill, RiErrorWarningFill } from '@remixicon/react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { X } from 'lucide-react';
import { useForm } from 'react-hook-form';
import { toast } from '@/lib/toast';
import { apiFetch } from '@/lib/api';
import {
  Alert,
  AlertDescription,
  AlertIcon,
  AlertTitle,
} from '@/components/ui/alert';
import { Badge, BadgeButton } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from '@/components/ui/form';
import { Input } from '@/components/ui/input';
import { ScrollArea } from '@/components/ui/scroll-area';
import { LoaderCircleIcon } from 'lucide-react';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { SearchableMultiSelect } from '@/components/common/SearchableMultiSelect';
import { Textarea } from '@/components/ui/textarea';
import { UserPermission, UserRole, UserRolePermission } from '@/app/models/user';
import { usePermissionSelectQuery } from '../../permissions/hooks/use-permission-select-query';
import { useRoleSelectQuery } from '../hooks/use-role-select-query';
import { RoleSchema, RoleSchemaType } from '../forms/role-schema';

const RoleEditDialog = ({
  open,
  closeDialog,
  role,
}: {
  open: boolean;
  closeDialog: () => void;
  role: UserRole | null;
}) => {
  const queryClient = useQueryClient();
  const [selectedPermissions, setSelectedPermissions] = useState<string[]>([]);
  const [sourceRoleId, setSourceRoleId] = useState<string>('');
  const [isCopyingPermissions, setIsCopyingPermissions] = useState(false);
  const [selectedUserToAssign, setSelectedUserToAssign] = useState<string>('');
  const [isAssigningUser, setIsAssigningUser] = useState(false);
  const [unassigningUserId, setUnassigningUserId] = useState<string | null>(null);
  const { data: permissionList } = usePermissionSelectQuery();
  const {
    data: roleList,
    error: roleListError,
    refetch: refetchRoleList,
  } = useRoleSelectQuery();
  const {
    data: usersSelect,
    error: usersSelectError,
    refetch: refetchUsersSelect,
  } = useQuery({
    queryKey: ['users-select'],
    queryFn: async () => {
      const response = await apiFetch('/api/user-management/users/select');
      if (!response.ok) {
        throw new Error('Failed to fetch users.');
      }
      return response.json();
    },
    enabled: open,
    staleTime: 1000 * 60 * 5,
  });

  const { data: assignedUsersData } = useQuery({
    queryKey: ['role-assigned-users', role?.id],
    queryFn: async () => {
      if (!role?.id) return [];
      const params = new URLSearchParams({
        roleId: role.id,
        page: '1',
        limit: '100',
      });
      const response = await apiFetch(`/api/user-management/users?${params.toString()}`);
      if (!response.ok) {
        throw new Error('Failed to fetch users assigned to this role.');
      }
      const payload = await response.json();
      return payload?.data ?? payload ?? [];
    },
    enabled: open && !!role?.id,
    staleTime: 1000 * 60 * 2,
  });

  const assignedUsers = (assignedUsersData ?? []) as Array<{
    id: string;
    name?: string | null;
    email?: string | null;
  }>;

  const form = useForm<RoleSchemaType>({
    resolver: zodResolver(RoleSchema),
    defaultValues: {
      name: '',
      slug: '',
      description: '',
      permissions: [],
    },
    mode: 'onTouched',
  });

  useEffect(() => {
    if (open) {
      const permissionIds: string[] = role?.permissions?.map((p) => p.id) ?? [];

      form.reset({
        name: role?.name || '',
        slug: role?.slug || '',
        description: role?.description ?? '',
        permissions: permissionIds,
      });
      setSourceRoleId('');
      setSelectedUserToAssign('');
      setSelectedPermissions(permissionIds);
    }
  }, [form, open, role]);

  useEffect(() => {
    form.setValue('permissions', selectedPermissions, { shouldDirty: true });
    form.trigger('permissions');
  }, [form, selectedPermissions]);

  const mutation = useMutation({
    mutationFn: async (values: RoleSchemaType) => {
      const isEdit = !!role?.id;
      const url = isEdit
        ? `/api/user-management/roles/${role.id}`
        : '/api/user-management/roles';
      const method = isEdit ? 'PUT' : 'POST';

      const response = await apiFetch(url, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(values),
      });

      if (!response.ok) {
        const { message } = await response.json();
        throw new Error(message);
      }

      return response.json();
    },
    onSuccess: () => {
      const isEdit = !!role?.id;
      const message = isEdit
        ? 'Role updated successfully'
        : 'Role added successfully';

      toast.custom(
        () => (
          <Alert variant="mono" icon="success">
            <AlertIcon>
              <RiCheckboxCircleFill />
            </AlertIcon>
            <AlertTitle>{message}</AlertTitle>
          </Alert>
        ),
        {
          position: 'top-center',
          duration: 1000 * 5, // 5 seconds
        },
      );

      queryClient.invalidateQueries({ queryKey: ['user-roles'] });
      queryClient.invalidateQueries({ queryKey: ['user-role-select'] });
      closeDialog();
    },
    onError: (error: Error) => {
      const message = error.message;
      toast.custom(
        () => (
          <Alert variant="mono" icon="destructive">
            <AlertIcon>
              <RiErrorWarningFill />
            </AlertIcon>
            <AlertTitle>{message}</AlertTitle>
          </Alert>
        ),
        {
          position: 'top-center',
        },
      );
    },
  });

  const isProcessing = mutation.status === 'pending';

  const handleSubmit = (values: RoleSchemaType) => {
    const payload = {
      ...values,
      permissions: selectedPermissions || [],
    };
    mutation.mutate(payload);
  };

  const togglePermissionSelection = (permissionId: string) => {
    setSelectedPermissions((prev) =>
      prev.includes(permissionId)
        ? prev.filter((id) => id !== permissionId)
        : [...prev, permissionId],
    );
  };

  const selectAllPermissions = () => {
    const allIds = (permissionList ?? []).map((p: UserPermission) => p.id);
    setSelectedPermissions(allIds);
  };

  const clearAllPermissions = () => {
    setSelectedPermissions([]);
  };

  const copyPermissionsFromRole = async (selectedRoleId: string) => {
    setSourceRoleId(selectedRoleId);

    if (!selectedRoleId) {
      return;
    }

    setIsCopyingPermissions(true);
    try {
      const response = await apiFetch(`/api/user-management/roles/${selectedRoleId}`);
      if (!response.ok) {
        const data = await response.json().catch(() => null);
        throw new Error(data?.message ?? 'Failed to copy permissions.');
      }

      const payload = await response.json();
      const selectedRole = ((payload?.data ?? payload) as UserRole) || null;
      const rolePermissions = (selectedRole?.permissions ?? []) as Array<
        UserRolePermission | UserPermission | string
      >;
      const permissionIds = Array.from(
        new Set(
          rolePermissions
            .map(
              (item) => {
                if (typeof item === 'string') {
                  return item;
                }

                return (
                  (item as { permissionId?: string }).permissionId ??
                  (item as { permission?: { id?: string } }).permission?.id ??
                  (item as { id?: string }).id
                );
              },
            )
            .filter((id): id is string => Boolean(id)),
        ),
      );

      setSelectedPermissions(permissionIds);
      toast.success(
        `Copied ${permissionIds.length} permission${permissionIds.length === 1 ? '' : 's'} from ${selectedRole?.name ?? 'selected role'}.`,
        { position: 'top-center' },
      );
    } catch (error) {
      const message =
        error instanceof Error ? error.message : 'Failed to copy permissions.';
      toast.error(message, { position: 'top-center' });
    } finally {
      setIsCopyingPermissions(false);
    }
  };

  const assignSelectedUserToRole = async () => {
    if (!role?.id || !selectedUserToAssign) {
      return;
    }

    setIsAssigningUser(true);
    try {
      const userResponse = await apiFetch(`/api/user-management/users/${selectedUserToAssign}`);
      if (!userResponse.ok) {
        const err = await userResponse.json().catch(() => ({}));
        throw new Error(err?.message ?? err?.detail ?? 'Failed to load selected user.');
      }

      const userPayload = await userResponse.json();
      const selectedUser = userPayload?.data ?? userPayload ?? {};
      const existingRoleIds = Array.from(
        new Set([
          ...((selectedUser?.roles ?? []).map((r: { id: string }) => r.id) ?? []),
          ...(selectedUser?.roleId ? [selectedUser.roleId] : []),
        ]),
      ) as string[];

      if (existingRoleIds.includes(role.id)) {
        toast.info('User is already assigned to this role.', { position: 'top-center' });
        return;
      }

      const response = await apiFetch(`/api/user-management/users/${selectedUserToAssign}/roles`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ role_ids: [...existingRoleIds, role.id] }),
      });

      if (!response.ok) {
        const err = await response.json().catch(() => ({}));
        throw new Error(err?.message ?? err?.detail ?? 'Failed to assign user to role.');
      }

      const assignedUserLabel =
        selectedUser?.name || selectedUser?.email || 'User';
      toast.success(`${assignedUserLabel} assigned to ${role.name}.`, {
        position: 'top-center',
      });
      setSelectedUserToAssign('');
      queryClient.invalidateQueries({ queryKey: ['role-assigned-users', role.id] });
      queryClient.invalidateQueries({ queryKey: ['user-users'] });
    } catch (error) {
      const message =
        error instanceof Error ? error.message : 'Failed to assign user.';
      toast.error(message, { position: 'top-center' });
    } finally {
      setIsAssigningUser(false);
    }
  };

  const unassignUserFromRole = async (userId: string, userLabel: string) => {
    if (!role?.id || !userId) {
      return;
    }

    setUnassigningUserId(userId);
    try {
      const userResponse = await apiFetch(`/api/user-management/users/${userId}`);
      if (!userResponse.ok) {
        const err = await userResponse.json().catch(() => ({}));
        throw new Error(err?.message ?? err?.detail ?? 'Failed to load user.');
      }

      const userPayload = await userResponse.json();
      const selectedUser = userPayload?.data ?? userPayload ?? {};
      const existingRoleIds = Array.from(
        new Set([
          ...((selectedUser?.roles ?? []).map((r: { id: string }) => r.id) ?? []),
          ...(selectedUser?.roleId ? [selectedUser.roleId] : []),
        ]),
      ) as string[];

      const nextRoleIds = existingRoleIds.filter((id) => id !== role.id);
      if (nextRoleIds.length === existingRoleIds.length) {
        toast.info(`${userLabel} is not assigned to this role.`, {
          position: 'top-center',
        });
        return;
      }

      const response = await apiFetch(`/api/user-management/users/${userId}/roles`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ role_ids: nextRoleIds }),
      });

      if (!response.ok) {
        const err = await response.json().catch(() => ({}));
        throw new Error(err?.message ?? err?.detail ?? 'Failed to unassign user from role.');
      }

      toast.success(`${userLabel} removed from ${role.name}.`, {
        position: 'top-center',
      });
      queryClient.invalidateQueries({ queryKey: ['role-assigned-users', role.id] });
      queryClient.invalidateQueries({ queryKey: ['user-users'] });
    } catch (error) {
      const message =
        error instanceof Error ? error.message : 'Failed to unassign user.';
      toast.error(message, { position: 'top-center' });
    } finally {
      setUnassigningUserId(null);
    }
  };

  return (
    <Dialog open={open} onOpenChange={closeDialog}>
      <DialogContent className="max-h-[90vh] overflow-hidden flex flex-col">
        <DialogHeader className="shrink-0">
          <DialogTitle>{role ? 'Edit Role' : 'Add Role'}</DialogTitle>
        </DialogHeader>
        <Form {...form}>
          <form
            onSubmit={form.handleSubmit(handleSubmit)}
            className="flex flex-1 min-h-0 flex-col overflow-hidden"
          >
            <div className="flex-1 min-h-0 overflow-y-auto overflow-x-hidden space-y-6 pr-1">
              {mutation.status === 'error' && (
                <Alert variant="destructive">
                  <AlertDescription>{mutation.error.message}</AlertDescription>
                </Alert>
              )}
              <FormField
                control={form.control}
                name="name"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Role Name</FormLabel>
                    <FormControl>
                      <Input placeholder="Enter role name" {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="slug"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Slug</FormLabel>
                    <FormControl>
                      <Input
                        placeholder="E.g: users:delete"
                        {...field}
                        disabled={!!role}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="description"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Description</FormLabel>
                    <FormControl>
                      <Textarea placeholder="Optional description" {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              {role?.id && (
                <FormItem>
                  <FormLabel>Assigned Users</FormLabel>
                  <ScrollArea className="h-[140px] rounded-md border border-input">
                    <div className="flex items-center flex-wrap gap-1.5 p-3 text-2sm text-muted-foreground">
                      {assignedUsers.length > 0 ? (
                        assignedUsers.map((user) => (
                          <Badge key={user.id} variant="secondary">
                            {user.name || user.email || user.id}
                            <BadgeButton
                              disabled={unassigningUserId === user.id}
                              onClick={() =>
                                unassignUserFromRole(
                                  user.id,
                                  user.name || user.email || user.id,
                                )
                              }
                            >
                              <X />
                            </BadgeButton>
                          </Badge>
                        ))
                      ) : (
                        <span className="text-sm text-muted-foreground">
                          No users assigned.
                        </span>
                      )}
                    </div>
                  </ScrollArea>
                  <div className="flex flex-wrap items-center gap-2 pt-1">
                    <SearchableSelect
                      value={selectedUserToAssign}
                      onChange={setSelectedUserToAssign}
                      disabled={isAssigningUser}
                      placeholder="Select user to assign"
                      emptyMessage="No user found."
                      triggerClassName="w-full sm:w-[22rem]"
                      loadError={usersSelectError}
                      onRetry={() => void refetchUsersSelect()}
                      options={(usersSelect ?? []).map(
                        (user: { id: string; name?: string | null; email?: string | null }) => ({
                          value: user.id,
                          label: user.name || user.email || user.id,
                          // Searchable by both, as the hand-rolled version was.
                          searchText: `${user.name ?? ''} ${user.email ?? ''}`.trim() || user.id,
                        }),
                      )}
                    />
                    <Button
                      type="button"
                      variant="outline"
                      onClick={assignSelectedUserToRole}
                      disabled={!selectedUserToAssign || isAssigningUser}
                    >
                      {isAssigningUser && <LoaderCircleIcon className="animate-spin" />}
                      Assign user
                    </Button>
                  </div>
                </FormItem>
              )}
              <FormItem>
                <FormLabel>Copy permissions from role</FormLabel>
                <SearchableSelect
                  value={sourceRoleId}
                  onChange={copyPermissionsFromRole}
                  disabled={isCopyingPermissions || !roleList?.length}
                  placeholder="Select a role to copy from"
                  loadError={roleListError}
                  onRetry={() => void refetchRoleList()}
                  options={(roleList ?? []).map((sourceRole: UserRole) => ({
                    value: sourceRole.id,
                    label: sourceRole.name,
                  }))}
                />
              </FormItem>
              <FormField
                control={form.control}
                name="permissions"
                render={() => (
                  <FormItem>
                    <FormLabel>Permissions</FormLabel>
                    <ScrollArea className="h-[200px] rounded-md border border-input">
                      <div className="flex items-center flex-wrap gap-1.5 p-3 text-2sm text-muted-foreground">
                        {selectedPermissions.length > 0 ? (
                          selectedPermissions.map((permissionId) => {
                            const permission = permissionList?.find(
                              (p: UserPermission) => p.id === permissionId,
                            );
                            return (
                              <Badge key={permissionId} variant="secondary">
                                {permission?.slug}
                                <BadgeButton
                                  onClick={() =>
                                    togglePermissionSelection(permissionId)
                                  }
                                >
                                  <X />
                                </BadgeButton>
                              </Badge>
                            );
                          })
                        ) : (
                          <span className="text-sm text-muted-foreground">
                            No permissions assigned.
                          </span>
                        )}
                      </div>
                    </ScrollArea>
                    <div className="flex flex-wrap items-center gap-2 pt-1">
                      <FormControl>
                        <SearchableMultiSelect
                          value={selectedPermissions}
                          onChange={setSelectedPermissions}
                          placeholder="Add Permissions"
                          emptyMessage="No permissions found."
                          className="min-w-[20rem] max-w-[36rem]"
                          // The chips above are the real display of what's selected, so the
                          // trigger stays a plain label instead of duplicating them.
                          renderTriggerLabel={() => 'Add Permissions'}
                          options={(permissionList ?? []).map((permission: UserPermission) => ({
                            value: permission.id,
                            label: permission.slug,
                          }))}
                        />
                      </FormControl>
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        onClick={selectAllPermissions}
                        disabled={!permissionList?.length}
                      >
                        Select all
                      </Button>
                      {selectedPermissions.length > 0 && (
                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          onClick={clearAllPermissions}
                        >
                          Clear all
                        </Button>
                      )}
                    </div>
                    <FormMessage />
                  </FormItem>
                )}
              />
            </div>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={closeDialog}>
                Cancel
              </Button>
              <Button type="submit" disabled={isProcessing}>
                {isProcessing && <LoaderCircleIcon className="animate-spin" />}
                {role ? 'Update Role' : 'Create Role'}
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
};

export default RoleEditDialog;
