'use client';

import { ChangeEvent, useEffect, useMemo, useRef, useState } from 'react';
import { zodResolver } from '@hookform/resolvers/zod';
import { RiCheckboxCircleFill, RiErrorWarningFill } from '@remixicon/react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ControllerRenderProps, FieldErrors, useForm } from 'react-hook-form';
import { toast } from '@/lib/toast';
import {
  AUTO_COLLECT_DAYS_DEFAULT,
  AUTO_COLLECT_DAYS_MAX,
  AUTO_COLLECT_DAYS_MIN,
} from '@/lib/dealer-kit/print-collection';
import { apiFetch } from '@/lib/api';
import { Alert, AlertIcon, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
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
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { SearchableMultiSelect } from '@/components/common/SearchableMultiSelect';
import { LoaderCircleIcon } from 'lucide-react';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import { useSettings } from './components/settings-context';
import { SigninBackgroundCard } from './components/signin-background-card';
import { CostPriceVerificationCard } from './components/cost-price-verification-card';
import TimezoneSelect from './components/timezone-select';
import {
  GeneralSettingsSchema,
  GeneralSettingsSchemaType,
  NO_DEFAULT_APPROVER_VALUE,
  NO_DEFAULT_RESERVE_POOL_VALUE,
  NO_DEFAULT_SUPPLIER_VALUE,
  NO_DEFAULT_UOM_VALUE,
} from './forms/general-settings-schema';
import { useUOMSelectQuery } from '@/app/(protected)/master-data-management/shared/hooks/use-uom-select-query';
import {
  getUsersForApproverSelect,
  type UserForSelect,
} from '@/app/(protected)/procurement-management/purchase-requests/services/purchaseRequestService';
import { getProductClassLabels } from '@/app/(protected)/master-data-management/product-categories/services/categoryService';
import { usePoolWarehouseSelectQuery } from './hooks/usePoolWarehouseSelectQuery';

type SupplierSelectRow = {
  id: string;
  supplier_code: string;
  supplier_name: string;
};

function mergeApproverOptions(
  options: UserForSelect[],
  savedId: string | null | undefined,
  savedName: string | null | undefined,
  savedEmail: string | null | undefined,
): UserForSelect[] {
  const byId = new Map(options.map((u) => [u.id, u]));
  if (savedId && !byId.has(savedId)) {
    byId.set(savedId, {
      id: savedId,
      name: savedName ?? 'Saved user',
      email: savedEmail ?? '-',
    });
  }
  return Array.from(byId.values());
}

const languages = [
  {
    code: 'en',
    name: 'English',
    shortName: 'EN',
    direction: 'ltr',
    flag: '/media/flags/united-states.svg',
  },
  {
    code: 'ar',
    name: 'Arabic',
    shortName: 'AR',
    direction: 'rtl',
    flag: '/media/flags/saudi-arabia.svg',
  },
  {
    code: 'es',
    name: 'Spanish',
    shortName: 'ES',
    direction: 'ltr',
    flag: '/media/flags/spain.svg',
  },
  {
    code: 'de',
    name: 'German',
    shortName: 'DE',
    direction: 'ltr',
    flag: '/media/flags/germany.svg',
  },
  {
    code: 'ch',
    name: 'Chinese',
    shortName: 'CH',
    direction: 'ltr',
    flag: '/media/flags/china.svg',
  },
];

export default function Page() {
  const { settings } = useSettings();
  const queryClient = useQueryClient();
  const {
    data: supplierOptions = [],
    error: supplierOptionsError,
    refetch: refetchSupplierOptions,
  } = useQuery({
    queryKey: ['suppliers-select-for-settings'],
    queryFn: async () => {
      const r = await apiFetch('/api/procurement/suppliers/select');
      if (!r.ok) throw new Error('Failed to load suppliers');
      return r.json() as Promise<SupplierSelectRow[]>;
    },
    staleTime: 5 * 60 * 1000,
  });

  const { data: approverUserOptions = [] } = useQuery({
    queryKey: ['users-select-for-settings-approvers'],
    queryFn: () => getUsersForApproverSelect(),
    staleTime: 5 * 60 * 1000,
  });

  // The units master is eight rows, so the shared static select is the right shape here -
  // no server search, no paging, and it is the same list every other UoM picker reads.
  const {
    data: uomOptions = [],
    error: uomOptionsError,
    refetch: refetchUomOptions,
  } = useUOMSelectQuery();
  const { data: poolWarehouseOptions = [] } = usePoolWarehouseSelectQuery();

  // The distinct class labels categories are grouped by - a short, closed list,
  // so a static picker is the right shape (D2). A saved value that has since
  // disappeared from the list is kept by SearchableMultiSelect's own `value`,
  // so unsetting it stays possible.
  const { data: classLabels = [], isError: classLabelsFailed } = useQuery({
    queryKey: ['product-class-labels'],
    queryFn: () => getProductClassLabels(),
    staleTime: 5 * 60 * 1000,
  });

  const savedSupplierId = settings?.defaultProductSupplierId ?? null;
  const savedSupplierMissingFromList =
    !!savedSupplierId && !supplierOptions.some((s) => s.id === savedSupplierId);

  const {
    data: savedSupplierFallback,
    isFetching: savedSupplierFallbackFetching,
  } = useQuery({
    queryKey: ['supplier-detail-settings', savedSupplierId],
    enabled: savedSupplierMissingFromList && !!savedSupplierId,
    queryFn: async () => {
      const r = await apiFetch(`/api/procurement/suppliers/${savedSupplierId}`);
      if (!r.ok) return null;
      return r.json() as Promise<SupplierSelectRow>;
    },
    staleTime: 5 * 60 * 1000,
  });

  const approversForPurchaseRequest = useMemo(
    () =>
      mergeApproverOptions(
        approverUserOptions,
        settings?.purchaseRequestDefaultApproverUserId,
        settings?.purchaseRequestDefaultApproverName,
        settings?.purchaseRequestDefaultApproverEmail,
      ),
    [
      approverUserOptions,
      settings?.purchaseRequestDefaultApproverUserId,
      settings?.purchaseRequestDefaultApproverName,
      settings?.purchaseRequestDefaultApproverEmail,
    ],
  );

  const approversForSponsorshipForm = useMemo(
    () =>
      mergeApproverOptions(
        approverUserOptions,
        settings?.sponsorshipFormDefaultApproverUserId,
        settings?.sponsorshipFormDefaultApproverName,
        settings?.sponsorshipFormDefaultApproverEmail,
      ),
    [
      approverUserOptions,
      settings?.sponsorshipFormDefaultApproverUserId,
      settings?.sponsorshipFormDefaultApproverName,
      settings?.sponsorshipFormDefaultApproverEmail,
    ],
  );

  // While `savedSupplierFallbackFetching`, the saved supplier is deliberately left OUT
  // of the list rather than filled with a placeholder row (M5-02 review S7): a synthetic
  // `{ supplier_name: 'Loading supplier…' }` was a SELECTABLE option, not a loading
  // indicator. Leaving it out for that one tick means `SearchableSelect` cannot resolve
  // `value` to any option, so the trigger falls back to its placeholder - nothing
  // selectable appears until the fetch resolves, one way or the other.
  const suppliersForSelect = useMemo((): SupplierSelectRow[] => {
    const byId = new Map(supplierOptions.map((s) => [s.id, s]));
    if (savedSupplierId && savedSupplierMissingFromList && !savedSupplierFallbackFetching) {
      if (savedSupplierFallback) {
        byId.set(savedSupplierFallback.id, savedSupplierFallback);
      } else {
        byId.set(savedSupplierId, {
          id: savedSupplierId,
          supplier_code: '-',
          supplier_name: 'Saved supplier (unavailable)',
        });
      }
    }
    return Array.from(byId.values());
  }, [
    supplierOptions,
    savedSupplierFallback,
    savedSupplierId,
    savedSupplierMissingFromList,
    savedSupplierFallbackFetching,
  ]);
  const [logoExistingPreview, setLogoExistingPreview] = useState<string | null>(
    '',
  );
  const [logoAttachedPreview, setLogoAttachedPreview] = useState<string | null>(
    '',
  );
  const logoFileRef = useRef<HTMLInputElement | null>(null);

  // Ensure all fields have default values
  const transformedSettings: GeneralSettingsSchemaType = {
    ...settings,
    logoFile: null,
    logoAction: '',
    name: settings?.name || '',
    active: settings?.active || true,
    address: settings?.address || '',
    websiteURL: settings?.websiteURL || '',
    language: settings?.language || 'en',
    supportEmail: settings?.supportEmail || '',
    supportPhone: settings?.supportPhone || '',
    currency: settings?.currency || 'MYR',
    currencyFormat: settings?.currencyFormat || 'RM {value}',
    timezone: settings?.timezone || 'Europe/London',
    defaultProductSupplierId:
      settings?.defaultProductSupplierId &&
      settings.defaultProductSupplierId.length > 0
        ? settings.defaultProductSupplierId
        : NO_DEFAULT_SUPPLIER_VALUE,
    defaultProductStandardLeadTimeDays:
      settings?.defaultProductStandardLeadTimeDays ?? 90,
    defaultUomId:
      settings?.defaultUomId && settings.defaultUomId.length > 0
        ? settings.defaultUomId
        : NO_DEFAULT_UOM_VALUE,
    oiReserveDefaultPoolWarehouseId:
      settings?.oiReserveDefaultPoolWarehouseId &&
      settings.oiReserveDefaultPoolWarehouseId.length > 0
        ? settings.oiReserveDefaultPoolWarehouseId
        : NO_DEFAULT_RESERVE_POOL_VALUE,
    takeoverCooldownSeconds: settings?.takeoverCooldownSeconds ?? 60,
    formSlaGraceSeconds: settings?.formSlaGraceSeconds ?? 0,
    deferredDeleteSeconds: settings?.deferredDeleteSeconds ?? 10,
    deferredActionSeconds: settings?.deferredActionSeconds ?? 5,
    priceTagAutoCollectDays:
      settings?.priceTagAutoCollectDays ?? AUTO_COLLECT_DAYS_DEFAULT,
    // The rollout default (plan 5.1) when the blob carries no value yet.
    planGrain: settings?.planGrain ?? 'product',
    // Off is the shipped state (PLAN-local-buy-routing-toggle.md).
    localBuyRoutingEnabled: Boolean(settings?.localBuyRoutingEnabled),
    priceTagGuardedClasses: settings?.priceTagGuardedClasses ?? [],
    purchaseRequestDefaultApproverUserId:
      settings?.purchaseRequestDefaultApproverUserId &&
      settings.purchaseRequestDefaultApproverUserId.length > 0
        ? settings.purchaseRequestDefaultApproverUserId
        : NO_DEFAULT_APPROVER_VALUE,
    sponsorshipFormDefaultApproverUserId:
      settings?.sponsorshipFormDefaultApproverUserId &&
      settings.sponsorshipFormDefaultApproverUserId.length > 0
        ? settings.sponsorshipFormDefaultApproverUserId
        : NO_DEFAULT_APPROVER_VALUE,
  };

  useEffect(() => {
    if (settings?.logo) {
      // Assume settings.logo contains the URL of the saved logo
      setLogoExistingPreview(settings.logo);
      setLogoAttachedPreview(null);
    }
  }, [settings]);

  const form = useForm<GeneralSettingsSchemaType>({
    resolver: zodResolver(GeneralSettingsSchema),
    defaultValues: transformedSettings,
    mode: 'onTouched',
  });

  // When settings are refetched (e.g. after save), reset form so saved values show
  useEffect(() => {
    if (!settings) return;
    form.reset({
      ...settings,
      logoFile: null,
      logoAction: '',
      name: settings.name ?? '',
      active: settings.active ?? true,
      address: settings.address ?? '',
      websiteURL: settings.websiteURL ?? '',
      language: settings.language ?? 'en',
      supportEmail: settings.supportEmail ?? '',
      supportPhone: settings.supportPhone ?? '',
      currency: settings.currency ?? 'MYR',
      currencyFormat: settings.currencyFormat ?? 'RM {value}',
      timezone: settings.timezone ?? 'Europe/London',
      defaultProductSupplierId:
        settings.defaultProductSupplierId &&
        settings.defaultProductSupplierId.length > 0
          ? settings.defaultProductSupplierId
          : NO_DEFAULT_SUPPLIER_VALUE,
      defaultProductStandardLeadTimeDays:
        settings.defaultProductStandardLeadTimeDays ?? 90,
      defaultUomId:
        settings.defaultUomId && settings.defaultUomId.length > 0
          ? settings.defaultUomId
          : NO_DEFAULT_UOM_VALUE,
      oiReserveDefaultPoolWarehouseId:
        settings.oiReserveDefaultPoolWarehouseId &&
        settings.oiReserveDefaultPoolWarehouseId.length > 0
          ? settings.oiReserveDefaultPoolWarehouseId
          : NO_DEFAULT_RESERVE_POOL_VALUE,
      takeoverCooldownSeconds: settings.takeoverCooldownSeconds ?? 60,
      formSlaGraceSeconds: settings.formSlaGraceSeconds ?? 0,
      deferredDeleteSeconds: settings.deferredDeleteSeconds ?? 10,
      deferredActionSeconds: settings.deferredActionSeconds ?? 5,
      priceTagAutoCollectDays:
        settings.priceTagAutoCollectDays ?? AUTO_COLLECT_DAYS_DEFAULT,
      planGrain: settings.planGrain ?? 'product',
      localBuyRoutingEnabled: Boolean(settings.localBuyRoutingEnabled),
      priceTagGuardedClasses: settings.priceTagGuardedClasses ?? [],
      purchaseRequestDefaultApproverUserId:
        settings.purchaseRequestDefaultApproverUserId &&
        settings.purchaseRequestDefaultApproverUserId.length > 0
          ? settings.purchaseRequestDefaultApproverUserId
          : NO_DEFAULT_APPROVER_VALUE,
      sponsorshipFormDefaultApproverUserId:
        settings.sponsorshipFormDefaultApproverUserId &&
        settings.sponsorshipFormDefaultApproverUserId.length > 0
          ? settings.sponsorshipFormDefaultApproverUserId
          : NO_DEFAULT_APPROVER_VALUE,
    });
  }, [settings, form]);

  const mutation = useMutation({
    mutationFn: async (values: GeneralSettingsSchemaType) => {
      // Send JSON with snake_case so backend receives valid SystemSettingUpdate
      const body: Record<string, unknown> = {
        name: values.name,
        active: values.active,
        address: values.address ?? null,
        website_url: values.websiteURL ?? null,
        support_email: values.supportEmail,
        support_phone: values.supportPhone ?? null,
        language: values.language,
        timezone: values.timezone,
        currency: values.currency,
        currency_format: values.currencyFormat,
        default_product_supplier_id:
          values.defaultProductSupplierId === NO_DEFAULT_SUPPLIER_VALUE
            ? null
            : values.defaultProductSupplierId,
        default_product_standard_lead_time_days:
          values.defaultProductStandardLeadTimeDays,
        default_uom_id:
          values.defaultUomId === NO_DEFAULT_UOM_VALUE
            ? null
            : values.defaultUomId,
        oi_reserve_default_pool_warehouse_id:
          values.oiReserveDefaultPoolWarehouseId === NO_DEFAULT_RESERVE_POOL_VALUE
            ? null
            : values.oiReserveDefaultPoolWarehouseId,
        takeover_cooldown_seconds: values.takeoverCooldownSeconds,
        form_sla_grace_seconds: values.formSlaGraceSeconds,
        deferred_delete_seconds: values.deferredDeleteSeconds,
        deferred_action_seconds: values.deferredActionSeconds,
        // r9 D10.
        price_tag_auto_collect_days: values.priceTagAutoCollectDays,
        plan_grain: values.planGrain,
        local_buy_routing_enabled: values.localBuyRoutingEnabled,
        price_tag_guarded_classes: values.priceTagGuardedClasses,
        purchase_request_default_approver_user_id:
          values.purchaseRequestDefaultApproverUserId ===
          NO_DEFAULT_APPROVER_VALUE
            ? null
            : values.purchaseRequestDefaultApproverUserId,
        sponsorship_form_default_approver_user_id:
          values.sponsorshipFormDefaultApproverUserId ===
          NO_DEFAULT_APPROVER_VALUE
            ? null
            : values.sponsorshipFormDefaultApproverUserId,
      };

      const response = await apiFetch('/api/user-management/settings/general', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });

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
      queryClient.invalidateQueries({ queryKey: ['system-app-config'] });
    },
    onError: (error: Error) => {
      toast.custom(
        (t) => (
          <Alert
            variant="mono"
            icon="destructive"
            close={true}
            onClose={() => {
              toast.dismiss(t);
            }}
          >
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

  const isProcessing = mutation.status === 'pending';

  const handleRemoveLogo = () => {
    setLogoExistingPreview(null);
    form.trigger('logoFile');
    form.setValue('logoFile', null);
    form.setValue('logoAction', 'remove', { shouldDirty: true });
  };

  const handleCancelLogo = () => {
    setLogoAttachedPreview(null);
    if (settings.logo) {
      setLogoExistingPreview(settings.logo);
    }
    form.setValue('logoFile', null);
    form.setValue('logoAction', '', { shouldDirty: true });
  };

  const handleChangeLogo = (
    e: ChangeEvent<HTMLInputElement>,
    field: ControllerRenderProps<GeneralSettingsSchemaType, 'logoFile'>,
  ) => {
    const file = e.target.files?.[0] || null;
    field.onChange(file);
    form.trigger('logoFile');
    form.setValue('logoFile', file);
    form.setValue('logoAction', 'save', { shouldDirty: true });

    if (file) {
      const reader = new FileReader();
      reader.onload = () => setLogoAttachedPreview(reader.result as string);
      reader.readAsDataURL(file);
    } else {
      setLogoAttachedPreview(null);
    }
  };

  const handleFormReset = () => {
    form.reset(transformedSettings);
  };

  const handleSubmit = (values: GeneralSettingsSchemaType) => {
    mutation.mutate(values);
  };

  const handleError = (errors: FieldErrors<GeneralSettingsSchemaType>) => {
    // Cast keys as an array of keys of SocialSettingsSchemaType
    const keys = Object.keys(errors) as (keyof GeneralSettingsSchemaType)[];
    const firstErrorKey = keys[0];
    const firstErrorMessage = errors[firstErrorKey]?.message;

    if (firstErrorMessage) {
      toast.custom(
        (t) => (
          <Alert
            variant="mono"
            icon="destructive"
            close={true}
            onClose={() => {
              toast.dismiss(t);
            }}
          >
            <AlertIcon>
              <RiErrorWarningFill />
            </AlertIcon>
            <AlertTitle>
              Your form has errors. Please fix them before submitting!
            </AlertTitle>
          </Alert>
        ),
        {
          position: 'top-center',
        },
      );
    }
  };

  return (
    <div className="space-y-5">
      <Card>
        <CardHeader className="border-b border-border">
          <CardTitle>Social Settings</CardTitle>
        </CardHeader>
        <CardContent className="py-12">
          <Form {...form}>
            <form
              onSubmit={form.handleSubmit(handleSubmit, handleError)}
              className="space-y-6 lg:max-w-[600px] mx-auto"
            >
              {/* Logo */}
              <FormField
                control={form.control}
                name="logoFile"
                render={({ field }) => (
                  <FormItem>
                    <div className="flex items-center gap-5">
                      <div className="relative size-32 border rounded-lg overflow-hidden">
                        <img
                          src={
                            logoAttachedPreview ||
                            logoExistingPreview ||
                            '/media/ui/empty-image.svg'
                          }
                          alt="Logo preview"
                          className="object-cover size-full"
                        />
                      </div>
                      <div>
                        <FormLabel>Company Logo</FormLabel>
                        <FormControl className="my-1.5">
                          <div className="flex flex-col space-y-2">
                            <div className="flex space-x-2">
                              <Button
                                type="button"
                                variant="outline"
                                onClick={() => logoFileRef.current?.click()}
                              >
                                Attach Image
                              </Button>

                              {logoAttachedPreview ||
                              (!logoAttachedPreview &&
                                !logoExistingPreview &&
                                settings?.logo) ? (
                                <Button
                                  type="button"
                                  variant="outline"
                                  onClick={handleCancelLogo}
                                >
                                  Cancel
                                </Button>
                              ) : null}

                              {settings?.logo &&
                              logoExistingPreview &&
                              !logoAttachedPreview ? (
                                <Button
                                  type="button"
                                  variant="outline"
                                  onClick={handleRemoveLogo}
                                >
                                  Remove
                                </Button>
                              ) : null}
                            </div>
                            <input
                              ref={logoFileRef}
                              type="file"
                              accept="image/*"
                              className="hidden"
                              onChange={(e) => handleChangeLogo(e, field)}
                            />
                          </div>
                        </FormControl>
                        <FormDescription>
                          We support PNGs, JPEGs and GIFs under 1MB.
                        </FormDescription>
                        <FormMessage />
                      </div>
                    </div>
                  </FormItem>
                )}
              />

              {/* Company Name */}
              <FormField
                control={form.control}
                name="name"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Shop Name</FormLabel>
                    <FormControl>
                      <Input placeholder="Enter company name" {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />

              {/* Active */}
              <FormField
                control={form.control}
                name="active"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Shop Status</FormLabel>
                    <FormControl>
                      <div className="flex items-center space-x-2 rounded-lg bg-accent/60 p-4">
                        <Switch
                          id="active"
                          checked={field.value}
                          onCheckedChange={field.onChange}
                        />
                        <Label htmlFor="active">
                          Toggle the switch to enable or disable the
                          store&apos;s active status.
                        </Label>
                      </div>
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />

              {/* Address */}
              <FormField
                control={form.control}
                name="address"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Address</FormLabel>
                    <FormControl>
                      <Textarea
                        rows={3}
                        id="address"
                        value={field.value || ''}
                        onChange={field.onChange}
                        placeholder="Enter store address"
                      />
                    </FormControl>
                    <FormDescription>
                      Enter the full address of the store for customer
                      reference.
                    </FormDescription>
                    <FormMessage />
                  </FormItem>
                )}
              />

              {/* Language */}
              <FormField
                control={form.control}
                name="language"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Language</FormLabel>
                    <FormControl>
                      <SearchableSelect
                        onChange={field.onChange}
                        value={field.value}
                        placeholder="Select language"
                        options={languages.map((language) => ({
                          value: language.code,
                          label: language.name,
                        }))}
                        // Rows carry a flag image, which no option field can express.
                        renderOption={(opt) => {
                          const language = languages.find(
                            (l) => l.code === opt.value,
                          );
                          return (
                            <span className="flex w-full items-center justify-between gap-2.5">
                              <img
                                src={language?.flag}
                                alt={`${opt.label} flag`}
                                className="size-4 rounded-full"
                              />
                              <span className="grow">{opt.label}</span>
                            </span>
                          );
                        }}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />

              {/* Website */}
              <FormField
                control={form.control}
                name="websiteURL"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Website URL</FormLabel>
                    <FormControl>
                      <Input
                        id="websiteURL"
                        type="url"
                        placeholder="Enter website url"
                        value={field.value || ''}
                        onChange={field.onChange}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />

              {/* Support Email */}
              <FormField
                control={form.control}
                name="supportEmail"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Support Email</FormLabel>
                    <FormControl>
                      <Input
                        id="supportEmail"
                        type="email"
                        placeholder="Enter support email"
                        value={field.value || ''}
                        onChange={field.onChange}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />

              {/* Support Phone */}
              <FormField
                control={form.control}
                name="supportPhone"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Support Phone</FormLabel>
                    <FormControl>
                      <Input
                        id="supportPhone"
                        type="tel"
                        placeholder="Enter support phone"
                        value={field.value || ''}
                        onChange={field.onChange}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />

              {/* Currency */}
              <FormField
                control={form.control}
                name="currency"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Currency</FormLabel>
                    <FormControl>
                      <SearchableSelect
                        onChange={field.onChange}
                        value={field.value}
                        placeholder="Select currency"
                        options={[
                          { value: 'MYR', label: 'MYR - Malaysian Ringgit' },
                          { value: 'USD', label: 'USD - US Dollar' },
                          { value: 'EUR', label: 'EUR - Euro' },
                          { value: 'GBP', label: 'GBP - British Pound' },
                          { value: 'JPY', label: 'JPY - Japanese Yen' },
                          { value: 'INR', label: 'INR - Indian Rupee' },
                        ]}
                      />
                    </FormControl>
                    <FormDescription>
                      Select the currency used for transactions in your store.
                    </FormDescription>
                    <FormMessage />
                  </FormItem>
                )}
              />

              {/* Currency Format */}
              <FormField
                control={form.control}
                name="currencyFormat"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Currency Format</FormLabel>
                    <FormControl>
                      <SearchableSelect
                        onChange={field.onChange}
                        value={field.value}
                        placeholder="Select currency format"
                        options={[
                          { value: 'RM {value}', label: 'RM {value}' },
                          { value: '$ {value}', label: '$ {value}' },
                          { value: '{value} €', label: '{value} €' },
                          { value: '£ {value}', label: '£ {value}' },
                          { value: '¥ {value}', label: '¥ {value}' },
                          { value: '₹ {value}', label: '₹ {value}' },
                        ]}
                      />
                    </FormControl>
                    <FormDescription>
                      Choose how the currency is displayed (e.g., symbol before
                      or after value).
                    </FormDescription>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <FormField
                control={form.control}
                name="defaultProductSupplierId"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Default supplier (new products)</FormLabel>
                    <FormControl>
                      <SearchableSelect
                        key={`def-sup-${field.value}-${suppliersForSelect.length}-${savedSupplierFallback?.id ?? ''}`}
                        onChange={field.onChange}
                        value={field.value}
                        placeholder="Select supplier"
                        loadError={supplierOptionsError}
                        onRetry={() => void refetchSupplierOptions()}
                        options={[
                          {
                            value: NO_DEFAULT_SUPPLIER_VALUE,
                            label: 'Automatic',
                          },
                          ...suppliersForSelect.map((s) => ({
                            value: s.id,
                            label: `${s.supplier_code} - ${s.supplier_name}`,
                          })),
                        ]}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />

            <FormField
              control={form.control}
              name="deferredDeleteSeconds"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Delete countdown (seconds)</FormLabel>
                  <FormControl>
                    <Input
                      type="number"
                      min={1}
                      max={600}
                      value={field.value}
                      onChange={(e) => {
                        const v = e.target.value;
                        if (v === '') return;
                        const n = parseInt(v, 10);
                        if (!Number.isNaN(n)) field.onChange(n);
                      }}
                    />
                  </FormControl>
                  <FormDescription>
                    How long a delete counts down before it applies. Nothing is removed
                    during the wait, so the person who clicked can cancel it.
                  </FormDescription>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="deferredActionSeconds"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Change countdown (seconds)</FormLabel>
                  <FormControl>
                    <Input
                      type="number"
                      min={1}
                      max={600}
                      value={field.value}
                      onChange={(e) => {
                        const v = e.target.value;
                        if (v === '') return;
                        const n = parseInt(v, 10);
                        if (!Number.isNaN(n)) field.onChange(n);
                      }}
                    />
                  </FormControl>
                  <FormDescription>
                    The same wait for a change that can be set back again, such as
                    marking a delivery order as delivered.
                  </FormDescription>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="priceTagAutoCollectDays"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>
                    Auto-mark price tags collected after (days)
                  </FormLabel>
                  <FormControl>
                    <Input
                      type="number"
                      min={AUTO_COLLECT_DAYS_MIN}
                      max={AUTO_COLLECT_DAYS_MAX}
                      value={field.value}
                      onChange={(e) => {
                        const v = e.target.value;
                        if (v === '') return;
                        const n = parseInt(v, 10);
                        if (!Number.isNaN(n)) field.onChange(n);
                      }}
                    />
                  </FormControl>
                  <FormDescription>
                    An office-printed request waiting to be picked up closes
                    itself after this many days. 0 leaves it open.
                  </FormDescription>
                  <FormMessage />
                </FormItem>
              )}
            />

              <FormField
                control={form.control}
                name="defaultUomId"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Default unit of measure</FormLabel>
                    <FormControl>
                      <SearchableSelect
                        key={`def-uom-${field.value}-${uomOptions.length}`}
                        onChange={(v) =>
                          field.onChange(v || NO_DEFAULT_UOM_VALUE)
                        }
                        value={field.value}
                        placeholder="Select unit"
                        clearable
                        loadError={uomOptionsError}
                        onRetry={() => void refetchUomOptions()}
                        options={[
                          { value: NO_DEFAULT_UOM_VALUE, label: 'Automatic' },
                          ...uomOptions.map((u) => ({
                            value: u.id,
                            label: `${u.uom_code} - ${u.uom_name}`,
                          })),
                        ]}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <FormField
                control={form.control}
                name="oiReserveDefaultPoolWarehouseId"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Reserve dialog default location</FormLabel>
                    <FormControl>
                      <SearchableSelect
                        key={`def-reserve-pool-${field.value}-${poolWarehouseOptions.length}`}
                        onChange={(v) =>
                          field.onChange(v || NO_DEFAULT_RESERVE_POOL_VALUE)
                        }
                        value={field.value}
                        placeholder="Select pool"
                        clearable
                        options={[
                          { value: NO_DEFAULT_RESERVE_POOL_VALUE, label: "Row's own site pool" },
                          ...poolWarehouseOptions.map((w) => ({
                            value: w.id,
                            label: w.warehouse_code,
                          })),
                        ]}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <FormField
                control={form.control}
                name="defaultProductStandardLeadTimeDays"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Standard lead time (days)</FormLabel>
                    <FormControl>
                      <Input
                        type="number"
                        min={0}
                        max={10950}
                        value={field.value}
                        onChange={(e) => {
                          const v = e.target.value;
                          if (v === '') return;
                          const n = parseInt(v, 10);
                          if (!Number.isNaN(n)) field.onChange(n);
                        }}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <FormField
                control={form.control}
                name="takeoverCooldownSeconds"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Takeover cooldown (seconds)</FormLabel>
                    <FormControl>
                      <Input
                        type="number"
                        min={0}
                        max={3600}
                        value={field.value}
                        onChange={(e) => {
                          const v = e.target.value;
                          if (v === '') return;
                          const n = parseInt(v, 10);
                          if (!Number.isNaN(n)) field.onChange(n);
                        }}
                      />
                    </FormControl>
                    <FormDescription>
                      Delay before a takeover commits, during which the original
                      assignee can reject and the initiator can cancel. Set to 0
                      to take over instantly.
                    </FormDescription>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <FormField
                control={form.control}
                name="formSlaGraceSeconds"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Undo grace window (seconds)</FormLabel>
                    <FormControl>
                      <Input
                        type="number"
                        min={0}
                        max={600}
                        value={field.value}
                        onChange={(e) => {
                          const v = e.target.value;
                          if (v === '') return;
                          const n = parseInt(v, 10);
                          if (!Number.isNaN(n)) field.onChange(n);
                        }}
                      />
                    </FormControl>
                    <FormDescription>
                      Default wait before an in-app form action applies, for
                      every SLA stage that does not set its own. During the wait
                      nothing is written and nobody is notified, so the person
                      who clicked can take it back. 0 disables the wait
                      everywhere except stages that override it.
                    </FormDescription>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <FormField
                control={form.control}
                name="planGrain"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Plan grain</FormLabel>
                    <FormControl>
                      <SearchableSelect
                        value={field.value}
                        onChange={field.onChange}
                        placeholder="Product"
                        options={[
                          { value: 'product', label: 'Product' },
                          { value: 'location', label: 'Location' },
                        ]}
                      />
                    </FormControl>
                    <FormDescription>
                      Applies to runs created afterwards.
                    </FormDescription>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <FormField
                control={form.control}
                name="localBuyRoutingEnabled"
                render={({ field }) => (
                  <FormItem>
                    <FormControl>
                      <div className="flex items-center space-x-2 rounded-lg bg-accent/60 p-4">
                        <Switch
                          id="local-buy-routing-enabled"
                          checked={field.value}
                          onCheckedChange={field.onChange}
                        />
                        <Label htmlFor="local-buy-routing-enabled">
                          Local supplier Buys skip Order Inquiries
                        </Label>
                      </div>
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <FormField
                control={form.control}
                name="priceTagGuardedClasses"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Price tag guarded classes</FormLabel>
                    <FormControl>
                      <SearchableMultiSelect
                        value={field.value ?? []}
                        onChange={field.onChange}
                        options={classLabels.map((label) => ({
                          value: label,
                          label,
                        }))}
                        placeholder="None"
                        emptyMessage="No class labels found."
                      />
                    </FormControl>
                    {classLabelsFailed ? (
                      <p className="text-sm text-destructive">
                        Could not load the class list. Try reloading the page.
                      </p>
                    ) : null}
                    <FormDescription>
                      A line in one of these classes is flagged to marketing when
                      its package is missing.
                    </FormDescription>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <FormField
                control={form.control}
                name="purchaseRequestDefaultApproverUserId"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Default approver (purchase requests)</FormLabel>
                    <FormControl>
                      <SearchableSelect
                        key={`def-pr-appr-${field.value}-${approversForPurchaseRequest.length}`}
                        onChange={field.onChange}
                        value={field.value}
                        placeholder="None"
                        options={[
                          { value: NO_DEFAULT_APPROVER_VALUE, label: 'None' },
                          ...approversForPurchaseRequest.map((u) => ({
                            value: u.id,
                            label:
                              (u.name?.trim() || u.email) + ` (${u.email})`,
                          })),
                        ]}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <FormField
                control={form.control}
                name="sponsorshipFormDefaultApproverUserId"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Default approver (sponsorship forms)</FormLabel>
                    <FormControl>
                      <SearchableSelect
                        key={`def-sf-appr-${field.value}-${approversForSponsorshipForm.length}`}
                        onChange={field.onChange}
                        value={field.value}
                        placeholder="None"
                        options={[
                          { value: NO_DEFAULT_APPROVER_VALUE, label: 'None' },
                          ...approversForSponsorshipForm.map((u) => ({
                            value: u.id,
                            label:
                              (u.name?.trim() || u.email) + ` (${u.email})`,
                          })),
                        ]}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />

              {/* Timezone */}
              <FormField
                control={form.control}
                name="timezone"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Timezone</FormLabel>
                    <FormControl>
                      <TimezoneSelect
                        defaultValue={field.value}
                        onChange={field.onChange}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />

              {/* Action Buttons */}
              <div className="flex gap-2.5 justify-end">
                <Button
                  type="button"
                  variant="outline"
                  onClick={handleFormReset}
                >
                  Reset
                </Button>
                <Button
                  type="submit"
                  disabled={!form.formState.isDirty || isProcessing}
                >
                  {isProcessing && (
                    <LoaderCircleIcon className="animate-spin" />
                  )}
                  Save Settings
                </Button>
              </div>
            </form>
          </Form>
        </CardContent>
      </Card>
      {/* The one setting on this screen whose value is a file, so it saves on its own
        rather than through the JSON form above. */}
      <SigninBackgroundCard />
      {/* #1288: read by the cost price review page too, so it saves on its own click
          rather than waiting on the General form's Save Settings. */}
      <CostPriceVerificationCard />
    </div>
  );
}
