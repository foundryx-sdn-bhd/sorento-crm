'use client';

import React, { useEffect, useMemo, useState } from 'react';
import { usePathname, useRouter } from 'next/navigation';
import { useQuery } from '@tanstack/react-query';
import {
  Bell,
  Bot,
  Eye,
  FileClock,
  HeartPulse,
  // Aliased: bare `Image` shadows the DOM global inside this module.
  Image as ImageIcon,
  Mail,
  MessageSquareWarning,
  Plug,
  Ruler,
  Settings,
  Share2,
  SlidersHorizontal,
} from 'lucide-react';
import { apiFetch } from '@/lib/api';
import { apiError, retryUnlessRefused } from '@/lib/api-client';
import { QueryErrorState } from '@/components/common/LoadErrorState';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Container } from '@/components/common/container';
import { SectionSkeleton } from '@/components/common/SectionSkeleton';
import { PageHeader } from '@/components/common/PageHeader';
import { SystemSetting } from '@/app/models/system';
import { AUTO_COLLECT_DAYS_DEFAULT } from '@/lib/dealer-kit/print-collection';
import { SettingsProvider } from './components/settings-context';

type NavRoutes = Record<
  string,
  {
    title: string;
    /** Beside the label in the strip, so a scrolled tab is still identifiable. */
    icon: React.FC<React.SVGProps<SVGSVGElement>>;
    path: string;
  }
>;

/** Map API snake_case settings to frontend camelCase SystemSetting */
export function mapSettingsFromApi(
  raw: Record<string, unknown> | null,
): SystemSetting | null {
  if (!raw || typeof raw !== 'object') return null;
  return {
    ...raw,
    id: raw.id as string,
    name: (raw.name as string) ?? '',
    logo: (raw.logo as string | null) ?? null,
    // A new settings column reaches the FE only if it is in this manual mapper too.
    signinBackground: (raw.signin_background as string | null) ?? null,
    active: Boolean(raw.active),
    address: (raw.address as string | null) ?? null,
    websiteURL: (raw.website_url as string | null) ?? null,
    supportEmail: (raw.support_email as string | null) ?? null,
    supportPhone: (raw.support_phone as string | null) ?? null,
    language: (raw.language as string) ?? 'en',
    timezone: (raw.timezone as string) ?? 'Europe/London',
    currency: (raw.currency as string) ?? 'MYR',
    currencyFormat: (raw.currency_format as string) ?? 'RM {value}',
    defaultProductSupplierId:
      (raw.default_product_supplier_id as string | null) ?? null,
    defaultProductStandardLeadTimeDays:
      typeof raw.default_product_standard_lead_time_days === 'number'
        ? raw.default_product_standard_lead_time_days
        : 90,
    // A new settings column reaches the FE only if it is in this manual mapper too.
    defaultUomId: (raw.default_uom_id as string | null) ?? null,
    defaultUomCode: (raw.default_uom_code as string | null) ?? null,
    // A new settings column reaches the FE only if it is in this manual mapper too
    // (PLAN-oi-request-cs-reserve.md section 6c F1).
    oiReserveDefaultPoolWarehouseId:
      (raw.oi_reserve_default_pool_warehouse_id as string | null) ?? null,
    takeoverCooldownSeconds:
      typeof raw.takeover_cooldown_seconds === 'number'
        ? raw.takeover_cooldown_seconds
        : 60,
    formSlaGraceSeconds:
      typeof raw.form_sla_grace_seconds === 'number'
        ? raw.form_sla_grace_seconds
        : 0,
    // A new settings column reaches the FE only if it is in this manual mapper too.
    deferredDeleteSeconds:
      typeof raw.deferred_delete_seconds === 'number' ? raw.deferred_delete_seconds : 10,
    deferredActionSeconds:
      typeof raw.deferred_action_seconds === 'number' ? raw.deferred_action_seconds : 5,
    // A new settings column reaches the FE only if it is in this manual mapper
    // too. 0 is a real answer here (the sweep is off), so the check is on the
    // TYPE, never on truthiness.
    priceTagAutoCollectDays:
      typeof raw.price_tag_auto_collect_days === 'number'
        ? raw.price_tag_auto_collect_days
        : AUTO_COLLECT_DAYS_DEFAULT,
    planGrain: raw.plan_grain === 'location' ? 'location' : 'product',
    // A new settings column reaches the FE only if it is in this manual mapper too
    // (PLAN-local-buy-routing-toggle.md). Off by default.
    localBuyRoutingEnabled: Boolean(raw.local_buy_routing_enabled),
    purchaseRequestDefaultApproverUserId:
      (raw.purchase_request_default_approver_user_id as string | null) ?? null,
    purchaseRequestDefaultApproverName:
      (raw.purchase_request_default_approver_name as string | null) ?? null,
    purchaseRequestDefaultApproverEmail:
      (raw.purchase_request_default_approver_email as string | null) ?? null,
    sponsorshipFormDefaultApproverUserId:
      (raw.sponsorship_form_default_approver_user_id as string | null) ?? null,
    sponsorshipFormDefaultApproverName:
      (raw.sponsorship_form_default_approver_name as string | null) ?? null,
    sponsorshipFormDefaultApproverEmail:
      (raw.sponsorship_form_default_approver_email as string | null) ?? null,
    n8nAttachmentWebhookUrl:
      (raw.n8n_attachment_webhook_url as string | null) ?? null,
    n8nCrmChatOutboundWebhookUrl:
      (raw.n8n_crm_chat_outbound_webhook_url as string | null) ?? null,
    n8nStockInquiryReviseWebhookUrl:
      (raw.n8n_stock_inquiry_revise_webhook_url as string | null) ?? null,
    n8nCloseConvoWebhookUrl:
      (raw.n8n_close_convo_webhook_url as string | null) ?? null,
    complaintDoDeliveredNotifyTiers:
      (raw.complaint_do_delivered_notify_tiers as string) ?? '1,2',
    handlingLockEnabledTypes: Array.isArray(raw.handling_lock_enabled_types)
      ? (raw.handling_lock_enabled_types as string[])
      : [],
    // Price tag packages (D2). On the GET dict AND on SystemSettingUpdate
    // server-side, so it must appear in this manual mapper too or the picker
    // would always render the default rather than the saved value.
    priceTagGuardedClasses: Array.isArray(raw.price_tag_guarded_classes)
      ? (raw.price_tag_guarded_classes as string[])
      : ['Bathroom Furniture', 'Kitchen Sink'],
    // Portal submission revisions. Both columns are on the GET dict AND on
    // SystemSettingUpdate server-side; they must appear in this manual mapper too
    // or the UI would always render the default rather than the saved value.
    portalRevisionsEnabled:
      typeof raw.portal_revisions_enabled === 'boolean'
        ? raw.portal_revisions_enabled
        : true,
    portalMaxRevisions:
      typeof raw.portal_max_revisions === 'number'
        ? raw.portal_max_revisions
        : 2,
    healthDigestEnabled: Boolean(raw.health_digest_enabled),
    healthAlertsEnabled: Boolean(raw.health_alerts_enabled),
    healthNotifyRoleIds: Array.isArray(raw.health_notify_role_ids)
      ? (raw.health_notify_role_ids as string[])
      : [],
    healthNotifyUserIds: Array.isArray(raw.health_notify_user_ids)
      ? (raw.health_notify_user_ids as string[])
      : [],
    healthIntegrationFailThreshold:
      typeof raw.health_integration_fail_threshold === 'number'
        ? raw.health_integration_fail_threshold
        : 10,
    healthAuditVolumeFloor:
      typeof raw.health_audit_volume_floor === 'number'
        ? raw.health_audit_volume_floor
        : 0,
    chatLatencyTargetSeconds:
      typeof raw.chat_latency_p99_target_seconds === 'number'
        ? raw.chat_latency_p99_target_seconds
        : 10,
    chatLatencyPercentile:
      typeof raw.chat_latency_percentile === 'number'
        ? raw.chat_latency_percentile
        : 99,
    chatLatencyCeilingMultiplier:
      typeof raw.chat_latency_ceiling_multiplier === 'number'
        ? raw.chat_latency_ceiling_multiplier
        : 3,
    chatLatencyNoReplyMinutes:
      typeof raw.chat_latency_no_reply_minutes === 'number'
        ? raw.chat_latency_no_reply_minutes
        : 5,
    chatLatencyMinSample:
      typeof raw.chat_latency_min_sample === 'number'
        ? raw.chat_latency_min_sample
        : 30,
    smtp: (raw.smtp as SystemSetting['smtp']) ?? null,
  } as SystemSetting;
}

const fetchSettings = async () => {
  const response = await apiFetch('/api/user-management/settings');
  if (!response.ok) {
    throw await apiError(response, 'Failed to load settings');
  }
  const data = await response.json();
  return {
    settings: mapSettingsFromApi(data.settings ?? null),
    roles: data.roles ?? [],
  };
};

function createDefaultSettings(): SystemSetting {
  return {
    id: '',
    name: '',
    logo: null,
    signinBackground: null,
    active: true,
    address: null,
    websiteURL: null,
    supportEmail: null,
    supportPhone: null,
    language: 'en',
    timezone: 'Europe/London',
    currency: 'MYR',
    currencyFormat: 'RM {value}',
    defaultProductSupplierId: null,
    defaultProductStandardLeadTimeDays: 90,
    defaultUomId: null,
    defaultUomCode: null,
    oiReserveDefaultPoolWarehouseId: null,
    takeoverCooldownSeconds: 60,
    formSlaGraceSeconds: 0,
    deferredDeleteSeconds: 10,
    deferredActionSeconds: 5,
    priceTagAutoCollectDays: AUTO_COLLECT_DAYS_DEFAULT,
    planGrain: 'product',
    localBuyRoutingEnabled: false,
    purchaseRequestDefaultApproverUserId: null,
    purchaseRequestDefaultApproverName: null,
    purchaseRequestDefaultApproverEmail: null,
    sponsorshipFormDefaultApproverUserId: null,
    sponsorshipFormDefaultApproverName: null,
    sponsorshipFormDefaultApproverEmail: null,
    n8nAttachmentWebhookUrl: null,
    n8nCrmChatOutboundWebhookUrl: null,
    n8nStockInquiryReviseWebhookUrl: null,
    n8nCloseConvoWebhookUrl: null,
    notifyStockEmail: false,
    notifyStockWeb: false,
    notifyStockThreshold: 0,
    notifyStockRoleIds: [],
    notifyNewOrderEmail: false,
    notifyNewOrderWeb: false,
    notifyNewOrderRoleIds: [],
    notifyOrderStatusUpdateEmail: false,
    notifyOrderStatusUpdateWeb: false,
    notifyOrderStatusUpdateRoleIds: [],
    notifyPaymentFailureEmail: false,
    notifyPaymentFailureWeb: false,
    notifyPaymentFailureRoleIds: [],
    notifySystemErrorFailureEmail: false,
    notifySystemErrorWeb: false,
    notifySystemErrorRoleIds: [],
    complaintDoDeliveredNotifyTiers: '1,2',
    handlingLockEnabledTypes: [],
    priceTagGuardedClasses: ['Bathroom Furniture', 'Kitchen Sink'],
    portalRevisionsEnabled: true,
    portalMaxRevisions: 2,
    healthDigestEnabled: false,
    healthAlertsEnabled: false,
    healthNotifyRoleIds: [],
    healthNotifyUserIds: [],
    healthIntegrationFailThreshold: 10,
    healthAuditVolumeFloor: 0,
    chatLatencyTargetSeconds: 10,
    chatLatencyPercentile: 99,
    chatLatencyCeilingMultiplier: 3,
    chatLatencyNoReplyMinutes: 5,
    chatLatencyMinSample: 30,
    smtp: null,
  };
}

export default function Layout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();

  const {
    data: loaded,
    isLoading,
    error,
    refetch,
    isFetching,
  } = useQuery({
    queryKey: ['system-settings'],
    queryFn: fetchSettings,
    staleTime: Infinity,
    gcTime: 1000 * 60 * 60, // 60 minutes
    refetchOnReconnect: false,
    retry: retryUnlessRefused,
    // The layout renders the failure in place; no toast on top of it.
    meta: { silent: true },
  });
  const data = loaded ?? { settings: null, roles: [] };

  const roles = data.roles ?? [];
  const settings = data.settings ?? createDefaultSettings();

  const navRoutes = useMemo<NavRoutes>(
    () => ({
      general: {
        title: 'General',
        icon: Settings,
        path: '/user-management/settings',
      },
      notifications: {
        title: 'Notifications',
        icon: Bell,
        path: '/user-management/settings/notifications',
      },
      complaints: {
        title: 'Complaints',
        icon: MessageSquareWarning,
        path: '/user-management/settings/complaints',
      },
      'portal-revisions': {
        title: 'Portal Revisions',
        icon: FileClock,
        path: '/user-management/settings/portal-revisions',
      },
      chatbot: {
        title: 'Chatbot',
        icon: Bot,
        path: '/user-management/settings/chatbot',
      },
      'chatbot-media': {
        title: 'Chatbot Media',
        icon: ImageIcon,
        path: '/user-management/settings/chatbot-media',
      },
      'stock-visibility': {
        title: 'Stock Visibility',
        icon: Eye,
        path: '/user-management/settings/stock-visibility',
      },
      'spec-visibility': {
        title: 'Spec Visibility',
        icon: Ruler,
        path: '/user-management/settings/spec-visibility',
      },
      'search-ranking': {
        title: 'Search ranking',
        icon: SlidersHorizontal,
        path: '/user-management/settings/search-ranking',
      },
      'system-health': {
        title: 'System Health',
        icon: HeartPulse,
        path: '/user-management/settings/system-health',
      },
      smtp: {
        title: 'SMTP',
        icon: Mail,
        path: '/user-management/settings/smtp',
      },
      social: {
        title: 'Social',
        icon: Share2,
        path: '/user-management/settings/social',
      },
      integrations: {
        title: 'Integrations',
        icon: Plug,
        path: '/user-management/settings/integrations',
      },
    }),
    [],
  );

  // Local state to instantly update the active tab on click
  const [activeTab, setActiveTab] = useState<string>('');

  // Keep the local state in sync with the current pathname, in case navigation happens externally
  useEffect(() => {
    const found = Object.keys(navRoutes).find(
      (key) => pathname === navRoutes[key].path,
    );
    if (found) {
      setActiveTab(found);
    } else {
      setActiveTab('general');
    }
  }, [navRoutes, pathname]);

  // Handle tab click: update local state immediately and trigger navigation
  const handleTabClick = (key: string, path: string) => {
    setActiveTab(key);
    // Navigate after a short delay (or immediately) so that the UI updates first
    router.push(path);
  };

  if (isLoading) {
    return <SectionSkeleton rows={3} className="mx-auto max-w-md mt-[30%]" />;
  }

  // Defaults are not data (NEVER-STUCK-UI S3, lever L9). Without a successful read
  // the tabs would draw `createDefaultSettings()`, and their Save would write those
  // blanks over the real configuration, so no tab renders until the read succeeds.
  // A background refetch that fails over settings already loaded keeps them.
  if (error && !loaded) {
    return (
      <Container>
        <PageHeader title="Settings" />
        <QueryErrorState
          error={error}
          title="Could not load settings"
          onRetry={() => void refetch()}
          retrying={isFetching}
        />
      </Container>
    );
  }

  return (
    <SettingsProvider settings={settings} roles={roles}>
      <Container>
        <PageHeader title="Settings" />
      </Container>
      <Container>
        <Tabs defaultValue={activeTab} value={activeTab} className="space-y-5">
          {/* Eleven tabs: at 375 most of them were off-screen with nothing saying
              so. The strip scrolls now (S1) and each tab reads as the Users one. */}
          <TabsList>
            {Object.entries(navRoutes).map(
              ([key, { title, icon: Icon, path }]) => (
                <TabsTrigger
                  key={key}
                  value={key}
                  disabled={isLoading}
                  onClick={() => handleTabClick(key, path)}
                >
                  <Icon />
                  <span>{title}</span>
                </TabsTrigger>
              ),
            )}
          </TabsList>
        </Tabs>
        <div className="grow mt-5">{children}</div>
      </Container>
    </SettingsProvider>
  );
}
