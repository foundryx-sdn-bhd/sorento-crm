'use client';

import { useMemo } from 'react';
import { zodResolver } from '@hookform/resolvers/zod';
import { useForm } from 'react-hook-form';
import { useRouter } from 'next/navigation';
import { z } from 'zod';
import { LoaderCircleIcon, Save } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
  FormDescription,
} from '@/components/ui/form';
import { Input } from '@/components/ui/input';
import { NumberInput } from '@/components/ui/number-input';
import { numberFieldError } from '@/lib/number-field';
import { Textarea } from '@/components/ui/textarea';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import {
  useCreateCampaign,
  useUpdateCampaign,
  useCampaign,
  useCampaignTypes,
} from '../hooks/useCampaigns';
import {
  CAMPAIGN_STATUSES,
  type CampaignFormData,
} from '../types/campaign.types';

const CampaignSchema = z.object({
  campaign_code: z.string().min(1, 'Campaign code is required'),
  campaign_name: z.string().min(1, 'Campaign name is required'),
  campaign_type_id: z.string().min(1, 'Campaign type is required'),
  description: z.string().optional(),
  start_date: z.string().min(1, 'Start date is required'),
  end_date: z.string().optional(),
  budget: z.string().optional().superRefine((value, ctx) => {
    const message = numberFieldError(value, { min: 0, optional: true });
    if (message) ctx.addIssue({ code: z.ZodIssueCode.custom, message });
  }),
  target_audience: z.string().optional(),
  status: z.enum(['planning', 'active', 'completed', 'cancelled']),
});

type CampaignSchemaType = z.infer<typeof CampaignSchema>;

function toDateInput(d?: Date | string | null): string {
  if (!d) return '';
  const date = typeof d === 'string' ? new Date(d) : d;
  if (Number.isNaN(date.getTime())) return '';
  return date.toISOString().slice(0, 10);
}

interface CampaignFormProps {
  campaignId?: string;
  onSuccess?: () => void;
}

export default function CampaignForm({ campaignId, onSuccess }: CampaignFormProps) {
  const router = useRouter();
  const isEditMode = !!campaignId;
  const { data: campaign, isLoading: isLoadingCampaign } = useCampaign(campaignId || null);
  const { data: campaignTypes = [], isLoading: isLoadingTypes } = useCampaignTypes();
  const createMutation = useCreateCampaign();
  const updateMutation = useUpdateCampaign();

  // The campaign fills the form through `values`, not a reset scheduled in an
  // effect behind a `formInitialized` flag (S7-03), which meant a refetched
  // campaign never reached the inputs.
  const editValues = useMemo<CampaignSchemaType | undefined>(
    () =>
      campaign && isEditMode
        ? {
            campaign_code: campaign.campaign_code,
            campaign_name: campaign.campaign_name,
            campaign_type_id: campaign.campaign_type_id,
            description: campaign.description || '',
            start_date: toDateInput(campaign.start_date),
            end_date: toDateInput(campaign.end_date),
            budget: campaign.budget != null ? String(campaign.budget) : '',
            target_audience: campaign.target_audience || '',
            status: (campaign.status?.toLowerCase() as CampaignSchemaType['status']) || 'planning',
          }
        : undefined,
    [campaign, isEditMode],
  );

  const form = useForm<CampaignSchemaType>({
    resolver: zodResolver(CampaignSchema),
    defaultValues: {
      campaign_code: '',
      campaign_name: '',
      campaign_type_id: '',
      description: '',
      start_date: '',
      end_date: '',
      budget: '',
      target_audience: '',
      status: 'planning',
    },
    values: editValues,
    // A refetch arriving mid-edit updates the fields nobody has touched and
    // leaves the ones being typed in alone.
    resetOptions: { keepDirtyValues: true },
    // A field answers when the reader leaves it, not on submit.
    mode: 'onTouched',
  });

  const onSubmit = async (data: CampaignSchemaType) => {
    try {
      const payload: CampaignFormData = {
        campaign_code: data.campaign_code,
        campaign_name: data.campaign_name,
        campaign_type_id: data.campaign_type_id,
        description: data.description || undefined,
        start_date: new Date(data.start_date),
        end_date: data.end_date ? new Date(data.end_date) : null,
        budget: data.budget ? Number(data.budget) : undefined,
        target_audience: data.target_audience || undefined,
        status: data.status,
      };

      if (isEditMode && campaignId) {
        await updateMutation.mutateAsync({ id: campaignId, data: payload });
      } else {
        await createMutation.mutateAsync(payload);
      }

      if (onSuccess) {
        onSuccess();
      } else {
        router.push('/marketing-management/campaigns');
      }
    } catch (error) {
      console.error('Campaign form submission error:', error);
    }
  };

  if (isEditMode && isLoadingCampaign) {
    return (
      <div className="flex items-center justify-center p-8">
        <LoaderCircleIcon className="size-6 animate-spin" />
      </div>
    );
  }

  const isLoading = createMutation.isPending || updateMutation.isPending;

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-6">
        <Card>
          <CardHeader>
            <CardTitle>{isEditMode ? 'Edit Campaign' : 'Create Campaign'}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-6">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              <FormField
                control={form.control}
                name="campaign_code"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Campaign Code *</FormLabel>
                    <FormControl>
                      <Input placeholder="SUMMER-2026" {...field} disabled={isEditMode} />
                    </FormControl>
                    <FormDescription>Unique identifier for this campaign</FormDescription>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <FormField
                control={form.control}
                name="campaign_name"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Campaign Name *</FormLabel>
                    <FormControl>
                      <Input placeholder="Summer Sale 2026" {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              <FormField
                control={form.control}
                name="campaign_type_id"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Campaign Type *</FormLabel>
                    <FormControl>
                      <SearchableSelect
                        value={field.value}
                        onChange={field.onChange}
                        options={campaignTypes.map((t) => ({ value: t.id, label: t.type_name }))}
                        placeholder={isLoadingTypes ? 'Loading types…' : 'Select a type'}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <FormField
                control={form.control}
                name="status"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Status *</FormLabel>
                    <FormControl>
                      <SearchableSelect
                        value={field.value}
                        onChange={field.onChange}
                        options={CAMPAIGN_STATUSES.map((s) => ({ value: s.value, label: s.label }))}
                        placeholder="Select status"
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
            </div>

            <FormField
              control={form.control}
              name="description"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Description</FormLabel>
                  <FormControl>
                    <Textarea
                      placeholder="What is this campaign about?"
                      {...field}
                      value={field.value || ''}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              <FormField
                control={form.control}
                name="start_date"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Start Date *</FormLabel>
                    <FormControl>
                      <Input type="date" {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <FormField
                control={form.control}
                name="end_date"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>End Date</FormLabel>
                    <FormControl>
                      <Input type="date" {...field} value={field.value || ''} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              <FormField
                control={form.control}
                name="budget"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Budget (MYR)</FormLabel>
                    <FormControl>
                      <NumberInput
                        min="0"
                        step="0.01"
                        placeholder="0.00"
                        {...field}
                        value={field.value || ''}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <FormField
                control={form.control}
                name="target_audience"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Target Audience</FormLabel>
                    <FormControl>
                      <Input
                        placeholder="e.g. Existing customers"
                        {...field}
                        value={field.value || ''}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
            </div>

            <div className="flex justify-end gap-4 pt-4">
              <Button
                type="button"
                variant="outline"
                onClick={() => {
                  if (onSuccess) {
                    onSuccess();
                  } else {
                    router.push('/marketing-management/campaigns');
                  }
                }}
              >
                Cancel
              </Button>
              <Button type="submit" disabled={isLoading}>
                {isLoading ? (
                  <>
                    <LoaderCircleIcon className="size-4 animate-spin" />
                    Saving...
                  </>
                ) : (
                  <>
                    <Save className="size-4" />
                    {isEditMode ? 'Update Campaign' : 'Create Campaign'}
                  </>
                )}
              </Button>
            </div>
          </CardContent>
        </Card>
      </form>
    </Form>
  );
}
