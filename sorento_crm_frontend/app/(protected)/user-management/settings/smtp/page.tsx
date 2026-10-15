'use client';

import { useState } from 'react';
import { zodResolver } from '@hookform/resolvers/zod';
import { RiCheckboxCircleFill, RiErrorWarningFill } from '@remixicon/react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useForm } from 'react-hook-form';
import { toast } from '@/lib/toast';
import { apiFetch } from '@/lib/api';
import { Alert, AlertIcon, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import {
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from '@/components/ui/form';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Eye, EyeOff, LoaderCircleIcon } from 'lucide-react';
import { Switch } from '@/components/ui/switch';
import { useHasPermission } from '@/hooks/usePermissions';
import { useSettings } from '../components/settings-context';
import {
  SmtpSettingsSchema,
  SmtpSettingsSchemaType,
} from '../forms/smtp-settings-schema';

export default function SmtpSettingsPage() {
  const queryClient = useQueryClient();
  const { settings } = useSettings();
  // Save and the test send both need the secrets slug, not `.edit`: the SMTP login is a
  // credential (SETTINGS-PERM-GATE). Without it the tab is read-only.
  const canManage = useHasPermission('user_management.settings.secrets');
  const smtp = settings?.smtp;
  const [showPassword, setShowPassword] = useState(false);

  const defaultValues: SmtpSettingsSchemaType = {
    smtp_host: smtp?.smtp_host ?? '',
    smtp_port: smtp?.smtp_port ?? '587',
    smtp_secure: smtp?.smtp_secure ?? true,
    smtp_username: smtp?.smtp_username ?? '',
    smtp_password: '', // never prefill; update-only
    smtp_from: smtp?.smtp_from ?? '',
  };

  const form = useForm<SmtpSettingsSchemaType>({
    resolver: zodResolver(SmtpSettingsSchema),
    mode: 'onTouched',
    defaultValues,
    values: {
      ...defaultValues,
      smtp_host: smtp?.smtp_host ?? '',
      smtp_port: smtp?.smtp_port ?? '587',
      smtp_secure: smtp?.smtp_secure ?? true,
      smtp_username: smtp?.smtp_username ?? '',
      smtp_from: smtp?.smtp_from ?? '',
    },
  });

  const saveMutation = useMutation({
    mutationFn: async (values: SmtpSettingsSchemaType) => {
      const payload: Record<string, unknown> = {
        smtp_host: values.smtp_host || null,
        smtp_port: values.smtp_port || null,
        smtp_secure: values.smtp_secure,
        smtp_username: values.smtp_username || null,
        smtp_from: values.smtp_from || null,
      };
      if (values.smtp_password && values.smtp_password.trim()) {
        payload.smtp_password = values.smtp_password;
      }
      const response = await apiFetch('/api/user-management/settings/smtp', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (!response.ok) {
        const data = await response.json();
        throw new Error(data.detail || 'Failed to save SMTP settings');
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
            <AlertTitle>SMTP settings saved</AlertTitle>
          </Alert>
        ),
        { position: 'top-center' },
      );
      queryClient.invalidateQueries({ queryKey: ['system-settings'] });
    },
    onError: (error: Error) => {
      toast.custom(
        (t) => (
          <Alert
            variant="mono"
            icon="destructive"
            close={true}
            onClose={() => toast.dismiss(t)}
          >
            <AlertIcon>
              <RiErrorWarningFill />
            </AlertIcon>
            <AlertTitle>{error.message}</AlertTitle>
          </Alert>
        ),
        { position: 'top-center' },
      );
    },
  });

  const testMutation = useMutation({
    mutationFn: async () => {
      const response = await apiFetch('/api/user-management/settings/smtp/test', {
        method: 'POST',
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Test failed');
      return data;
    },
    onSuccess: (data: { success?: boolean; message?: string }) => {
      if (data.success) {
        toast.custom(
          () => (
            <Alert variant="mono" icon="success">
              <AlertIcon>
                <RiCheckboxCircleFill />
              </AlertIcon>
              <AlertTitle>{data.message ?? 'Connection successful'}</AlertTitle>
            </Alert>
          ),
          { position: 'top-center' },
        );
      } else {
        toast.custom(
          (t) => (
            <Alert
              variant="mono"
              icon="destructive"
              close={true}
              onClose={() => toast.dismiss(t)}
            >
              <AlertIcon>
                <RiErrorWarningFill />
              </AlertIcon>
              <AlertTitle>{data.message ?? 'Connection failed'}</AlertTitle>
            </Alert>
          ),
          { position: 'top-center' },
        );
      }
    },
    onError: (error: Error) => {
      toast.custom(
        (t) => (
          <Alert
            variant="mono"
            icon="destructive"
            close={true}
            onClose={() => toast.dismiss(t)}
          >
            <AlertIcon>
              <RiErrorWarningFill />
            </AlertIcon>
            <AlertTitle>{error.message}</AlertTitle>
          </Alert>
        ),
        { position: 'top-center' },
      );
    },
  });

  const onSubmit = (values: SmtpSettingsSchemaType) => saveMutation.mutate(values);

  return (
    <Card>
      <CardHeader className="border-b border-border">
        <CardTitle>SMTP (Email)</CardTitle>
        <CardDescription>
          Configure SMTP for notification emails. Password is stored securely and never shown.
        </CardDescription>
      </CardHeader>
      <CardContent className="py-8">
        <Form {...form}>
          <form
            onSubmit={form.handleSubmit(onSubmit)}
            className="space-y-6 lg:max-w-[600px]"
          >
            <fieldset disabled={!canManage} className="space-y-6">
              <FormField
                control={form.control}
                name="smtp_host"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Host</FormLabel>
                    <FormControl>
                      <Input
                        placeholder="smtp.example.com"
                        {...field}
                        value={field.value ?? ''}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="smtp_port"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Port</FormLabel>
                    <FormControl>
                      <Input placeholder="587" {...field} value={field.value ?? ''} />
                    </FormControl>
                    <FormDescription>Usually 587 (TLS) or 465 (SSL)</FormDescription>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="smtp_secure"
                render={({ field }) => (
                  <FormItem>
                    <div className="flex items-center space-x-2 rounded-lg bg-accent/60 p-4">
                      <Switch
                        id="smtp_secure"
                        checked={field.value}
                        onCheckedChange={field.onChange}
                      />
                      <Label htmlFor="smtp_secure">Use secure connection (SSL/TLS)</Label>
                    </div>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="smtp_username"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Username</FormLabel>
                    <FormControl>
                      <Input
                        type="text"
                        placeholder="user@example.com"
                        {...field}
                        value={field.value ?? ''}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="smtp_password"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Password</FormLabel>
                    <FormControl>
                      <div className="relative">
                        <Input
                          type={showPassword ? 'text' : 'password'}
                          placeholder="••••••••"
                          autoComplete="new-password"
                          {...field}
                          value={field.value ?? ''}
                          className="pr-10"
                        />
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          className="absolute right-0 top-0 h-full px-3 py-2 hover:bg-transparent"
                          onClick={() => setShowPassword((prev) => !prev)}
                          aria-label={showPassword ? 'Hide password' : 'Show password'}
                        >
                          {showPassword ? (
                            <EyeOff className="h-4 w-4 text-muted-foreground" />
                          ) : (
                            <Eye className="h-4 w-4 text-muted-foreground" />
                          )}
                        </Button>
                      </div>
                    </FormControl>
                    <FormDescription>Leave blank to keep existing password</FormDescription>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="smtp_from"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>From address</FormLabel>
                    <FormControl>
                      <Input
                        placeholder="noreply@example.com or Name &lt;noreply@example.com&gt;"
                        {...field}
                        value={field.value ?? ''}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
            </fieldset>
            {canManage && (
              <div className="flex gap-2.5 justify-end">
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => testMutation.mutate()}
                  disabled={testMutation.isPending}
                >
                  {testMutation.isPending && (
                    <LoaderCircleIcon className="animate-spin mr-2 h-4 w-4" />
                  )}
                  Test connection
                </Button>
                <Button
                  type="submit"
                  disabled={saveMutation.isPending}
                >
                  {saveMutation.isPending && (
                    <LoaderCircleIcon className="animate-spin mr-2 h-4 w-4" />
                  )}
                  Save SMTP settings
                </Button>
              </div>
            )}
          </form>
        </Form>
      </CardContent>
    </Card>
  );
}
