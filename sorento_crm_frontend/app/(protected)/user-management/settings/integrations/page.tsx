'use client';

import { useEffect } from 'react';
import { zodResolver } from '@hookform/resolvers/zod';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useForm } from 'react-hook-form';
import { z } from 'zod';
import { toast } from '@/lib/toast';
import { apiFetch } from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from '@/components/ui/form';
import { Input } from '@/components/ui/input';
import { LoaderCircleIcon } from 'lucide-react';
import { useHasPermission } from '@/hooks/usePermissions';
import { useSettings } from '../components/settings-context';
import RuntimeSettingsCards from './components/RuntimeSettingsCards';
import { RUNTIME_SETTINGS_PERMISSION } from './services/runtimeSettingsService';

const IntegrationsSchema = z.object({
  n8nAttachmentWebhookUrl: z.string(),
  n8nCrmChatOutboundWebhookUrl: z.string(),
  n8nStockInquiryReviseWebhookUrl: z.string(),
  n8nCloseConvoWebhookUrl: z.string(),
});

type IntegrationsForm = z.infer<typeof IntegrationsSchema>;

export default function IntegrationsSettingsPage() {
  const { settings } = useSettings();
  const canManageSecrets = useHasPermission(RUNTIME_SETTINGS_PERMISSION);
  const queryClient = useQueryClient();

  const form = useForm<IntegrationsForm>({
    resolver: zodResolver(IntegrationsSchema),
    mode: 'onTouched',
    defaultValues: {
      n8nAttachmentWebhookUrl: settings.n8nAttachmentWebhookUrl ?? '',
      n8nCrmChatOutboundWebhookUrl: settings.n8nCrmChatOutboundWebhookUrl ?? '',
      n8nStockInquiryReviseWebhookUrl: settings.n8nStockInquiryReviseWebhookUrl ?? '',
      n8nCloseConvoWebhookUrl: settings.n8nCloseConvoWebhookUrl ?? '',
    },
  });

  useEffect(() => {
    form.reset({
      n8nAttachmentWebhookUrl: settings.n8nAttachmentWebhookUrl ?? '',
      n8nCrmChatOutboundWebhookUrl: settings.n8nCrmChatOutboundWebhookUrl ?? '',
      n8nStockInquiryReviseWebhookUrl: settings.n8nStockInquiryReviseWebhookUrl ?? '',
      n8nCloseConvoWebhookUrl: settings.n8nCloseConvoWebhookUrl ?? '',
    });
  }, [settings, form]);

  const mutation = useMutation({
    mutationFn: async (values: IntegrationsForm) => {
      const response = await apiFetch('/api/user-management/settings/general', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          n8n_attachment_webhook_url: values.n8nAttachmentWebhookUrl.trim() || null,
          n8n_crm_chat_outbound_webhook_url:
            values.n8nCrmChatOutboundWebhookUrl.trim() || null,
          n8n_stock_inquiry_revise_webhook_url:
            values.n8nStockInquiryReviseWebhookUrl.trim() || null,
          n8n_close_convo_webhook_url: values.n8nCloseConvoWebhookUrl.trim() || null,
        }),
      });
      if (!response.ok) {
        const err = await response.json().catch(() => ({}));
        throw new Error((err as { detail?: string }).detail || 'Save failed');
      }
      return response.json();
    },
    onSuccess: () => {
      toast.success('Integration settings saved');
      queryClient.invalidateQueries({ queryKey: ['system-settings'] });
    },
    onError: (e: Error) => toast.error(e.message),
  });

  return (
    <div className="space-y-6">
    <Card>
      <CardHeader className="border-b border-border">
        <CardTitle>Integrations</CardTitle>
      </CardHeader>
      <CardContent className="py-8">
        <Form {...form}>
          <form
            onSubmit={form.handleSubmit((v) => mutation.mutate(v))}
            className="space-y-6 lg:max-w-[640px] mx-auto"
          >
            <FormField
              control={form.control}
              name="n8nAttachmentWebhookUrl"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Attachment webhook URL</FormLabel>
                  <FormControl>
                    <Input
                      type="url"
                      placeholder="https://…"
                      autoComplete="off"
                      {...field}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="n8nCrmChatOutboundWebhookUrl"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>CRM chat outbound webhook URL</FormLabel>
                  <FormControl>
                    <Input
                      type="url"
                      placeholder="https://…"
                      autoComplete="off"
                      {...field}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="n8nStockInquiryReviseWebhookUrl"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Stock inquiry revise webhook URL</FormLabel>
                  <FormControl>
                    <Input
                      type="url"
                      placeholder="https://…"
                      autoComplete="off"
                      {...field}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="n8nCloseConvoWebhookUrl"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Ticket resolve close webhook URL</FormLabel>
                  <FormControl>
                    <Input
                      type="url"
                      placeholder="https://…"
                      autoComplete="off"
                      {...field}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <Button type="submit" disabled={mutation.isPending}>
              {mutation.isPending ? (
                <>
                  <LoaderCircleIcon className="size-4 animate-spin mr-2" />
                  Saving…
                </>
              ) : (
                'Save'
              )}
            </Button>
          </form>
        </Form>
      </CardContent>
    </Card>
    {canManageSecrets && <RuntimeSettingsCards />}
    </div>
  );
}
