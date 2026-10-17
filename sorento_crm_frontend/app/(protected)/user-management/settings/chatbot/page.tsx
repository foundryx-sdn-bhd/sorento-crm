'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { RiErrorWarningFill } from '@remixicon/react';
import { LoaderCircleIcon } from 'lucide-react';

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { Alert, AlertIcon, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { Skeleton } from '@/components/ui/skeleton';
import { Switch } from '@/components/ui/switch';

import { useChatbotSettings, useSaveChatbotSettings } from './hooks/useChatbotSettings';
import {
  useChatbotMemorySettings,
  useChatbotTierOrder,
  useSaveChatbotMemorySettings,
  useSaveChatbotTierOrder,
} from './hooks/useChatbotMemoryAndTierOrder';
import type {
  ChatbotDirectIngressMode,
  ChatbotMemorySettings,
  ChatbotSettings,
} from './services/chatbotSettingsService';
import MemorySettingsCard from './components/MemorySettingsCard';
import TierOrderCard from './components/TierOrderCard';
import CrossDomainLadderCard, {
  DEFAULT_LADDER_DOMAIN,
} from './components/CrossDomainLadderCard';
import {
  useChatbotDomainsQuery,
  useUpdateChatbotDomain,
} from '@/app/(protected)/system-management/chatbot-domains/hooks/useChatbotDomains';
import type { ChatbotDomainInput } from '@/app/(protected)/system-management/chatbot-domains/types/chatbotDomain.types';

/**
 * Settings -> Chatbot (AC-809, AC-810; chatbot turn re-architecture S1/S5, AC-1513,
 * AC-1560).
 *
 * The three switches that used to be environment flags - read per turn by the engine,
 * so a change here takes effect on the next WhatsApp message with no deploy. The
 * domains the bot refuses used to be a free-text list here (AC-931); that box is
 * retired - "Supported" now lives on each domain's own row on the Chatbot Domains page,
 * which is also where a domain's tools, narrowing and escalation team live, so a
 * refusal is no longer a name typed twice.
 *
 * The "Lanes the CRM answers" card is retired (S3 ruling, 16 Sep 2026):
 * `chatbot_completed_lanes` no longer gates which turns the CRM finishes now that the
 * turn engine itself decides, so a checkbox here would no longer change anything the
 * next WhatsApp message does.
 */

// CHAT-SPEED: who the respond.io webhook starts turns for (owner ruling Q4 a).
const DIRECT_INGRESS_OPTIONS: { value: ChatbotDirectIngressMode; label: string }[] = [
  { value: 'off', label: 'Off' },
  { value: 'contacts', label: 'Listed contacts' },
  { value: 'all', label: 'All contacts' },
];

export default function ChatbotSettingsPage() {
  const settingsQuery = useChatbotSettings();
  const save = useSaveChatbotSettings();
  // One Save for the whole page (browser pass 1, 16 Sep 2026): the Memory and Tier
  // order cards used to carry a Save each, right under a Switches card that had none
  // of its own - so the nearest Save silently no-op'd the switches. The page owns all
  // three drafts now and the one button fires every PUT that has something to save.
  const memoryQuery = useChatbotMemorySettings();
  const saveMemory = useSaveChatbotMemorySettings();
  const tierOrderQuery = useChatbotTierOrder();
  const saveTierOrder = useSaveChatbotTierOrder();
  // The ladder is the "inventory" domain row's own `ladder` column, so its save is that
  // domain's PUT - fired by the same one button as the other three (browser pass 2,
  // step 4: the card's own Save was the second Save on the page).
  const domainsQuery = useChatbotDomainsQuery();
  const saveDomain = useUpdateChatbotDomain();

  const [draft, setDraft] = useState<ChatbotSettings | null>(null);
  const [memoryDraft, setMemoryDraft] = useState<ChatbotMemorySettings | null>(null);
  const [tierOrderDraft, setTierOrderDraft] = useState<string[] | null>(null);
  const [ladderDraft, setLadderDraft] = useState<string[] | null>(null);
  const [orderingConfirmOpen, setOrderingConfirmOpen] = useState(false);

  const ladderDomain = (domainsQuery.data ?? []).find((d) => d.name === DEFAULT_LADDER_DOMAIN);

  useEffect(() => {
    if (settingsQuery.data && draft === null) setDraft(settingsQuery.data);
  }, [settingsQuery.data, draft]);
  useEffect(() => {
    if (memoryQuery.data && memoryDraft === null) setMemoryDraft(memoryQuery.data);
  }, [memoryQuery.data, memoryDraft]);
  useEffect(() => {
    if (tierOrderQuery.data && tierOrderDraft === null) setTierOrderDraft(tierOrderQuery.data);
  }, [tierOrderQuery.data, tierOrderDraft]);
  useEffect(() => {
    if (ladderDomain && ladderDraft === null) setLadderDraft(ladderDomain.ladder);
  }, [ladderDomain, ladderDraft]);

  const saving =
    save.isPending || saveMemory.isPending || saveTierOrder.isPending || saveDomain.isPending;

  // The failed load is checked FIRST. A load that fails leaves `draft` null, so a
  // loading check that also covered `!draft` would win every time and the operator
  // would wait on skeletons that never resolve.
  if (settingsQuery.isError && !draft) {
    return (
      <Alert variant="mono" icon="destructive">
        <AlertIcon>
          <RiErrorWarningFill />
        </AlertIcon>
        <AlertTitle>
          Chatbot settings could not be loaded. Reload the page to try again.
        </AlertTitle>
      </Alert>
    );
  }

  if (settingsQuery.isLoading || !draft) {
    return (
      <div className="space-y-5">
        <Skeleton className="h-64 w-full" />
        <Skeleton className="h-48 w-full" />
      </div>
    );
  }

  const set = <K extends keyof ChatbotSettings>(key: K, value: ChatbotSettings[K]) =>
    setDraft((prev) => (prev ? { ...prev, [key]: value } : prev));

  return (
    <div className="space-y-5">
      <Card>
        <CardHeader className="border-b border-border">
          <CardTitle>Switches</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4 py-5">
          <div className="flex items-center justify-between gap-4">
            <Label htmlFor="chatbot-stock-denial" className="font-normal cursor-pointer">
              Stock denial lanes
            </Label>
            <Switch
              id="chatbot-stock-denial"
              checked={draft.chatbot_stock_denial_enabled}
              onCheckedChange={(next) => set('chatbot_stock_denial_enabled', next === true)}
            />
          </div>
          <div className="flex items-center justify-between gap-4">
            <Label htmlFor="chatbot-business-lane" className="font-normal cursor-pointer">
              Business lane
            </Label>
            <Switch
              id="chatbot-business-lane"
              checked={draft.chatbot_business_lane_enabled}
              onCheckedChange={(next) => set('chatbot_business_lane_enabled', next === true)}
            />
          </div>
          <div className="flex items-center justify-between gap-4">
            <Label htmlFor="chatbot-ordering" className="font-normal cursor-pointer">
              Ordering
            </Label>
            <Switch
              id="chatbot-ordering"
              checked={draft.chatbot_ordering_enabled}
              // Turning it ON is confirmed, turning it off is not: switching it on
              // retires the n8n tail, and a turn on a lane the CRM cannot finish then
              // has nobody left to answer it.
              onCheckedChange={(next) => {
                if (next === true) setOrderingConfirmOpen(true);
                else set('chatbot_ordering_enabled', false);
              }}
            />
          </div>
          <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between sm:gap-4">
            <Label htmlFor="chatbot-direct-ingress" className="font-normal">
              Direct ingress
            </Label>
            <div className="w-full sm:w-56">
              <SearchableSelect
                id="chatbot-direct-ingress"
                value={draft.chatbot_direct_ingress_mode}
                options={DIRECT_INGRESS_OPTIONS}
                onChange={(next) =>
                  set(
                    'chatbot_direct_ingress_mode',
                    (next || 'off') as ChatbotDirectIngressMode,
                  )
                }
              />
            </div>
          </div>
          {draft.chatbot_direct_ingress_mode === 'contacts' ? (
            <div className="space-y-2">
              <Label htmlFor="chatbot-direct-ingress-contacts" className="font-normal">
                Listed contacts
              </Label>
              <Textarea
                id="chatbot-direct-ingress-contacts"
                rows={3}
                value={draft.chatbot_direct_ingress_contacts ?? ''}
                placeholder="One respond.io contact id per line"
                onChange={(event) =>
                  set('chatbot_direct_ingress_contacts', event.target.value || null)
                }
              />
            </div>
          ) : null}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="border-b border-border">
          <CardTitle>Domains the bot does not answer</CardTitle>
        </CardHeader>
        <CardContent className="py-5">
          <p className="text-sm text-muted-foreground">
            Moved to each domain&apos;s own row -{' '}
            <Link
              href="/system-management/chatbot-domains"
              className="text-primary hover:underline"
            >
              Chatbot Domains
            </Link>
            . Turn the Supported switch off there instead of listing the name here.
          </p>
        </CardContent>
      </Card>

      <MemorySettingsCard
        value={memoryDraft}
        onChange={setMemoryDraft}
        isLoading={memoryQuery.isLoading}
        isError={memoryQuery.isError}
      />
      <TierOrderCard
        value={tierOrderDraft}
        onChange={setTierOrderDraft}
        isLoading={tierOrderQuery.isLoading}
        isError={tierOrderQuery.isError}
      />
      <CrossDomainLadderCard
        value={ladderDraft}
        onChange={setLadderDraft}
        domains={domainsQuery.data}
        isLoading={domainsQuery.isLoading}
        isError={domainsQuery.isError}
      />

      <div className="flex flex-col gap-3 sm:flex-row sm:justify-end">
        <Button
          type="button"
          variant="outline"
          disabled={saving}
          onClick={() => {
            if (settingsQuery.data) setDraft(settingsQuery.data);
            if (memoryQuery.data) setMemoryDraft(memoryQuery.data);
            if (tierOrderQuery.data) setTierOrderDraft(tierOrderQuery.data);
            if (ladderDomain) setLadderDraft(ladderDomain.ladder);
          }}
        >
          Reset
        </Button>
        <Button
          type="button"
          disabled={saving}
          onClick={() => {
            // Re-seed each draft from what came back, not from what was typed: the
            // row the backend returns is what was actually persisted.
            save.mutate(draft, { onSuccess: (saved) => setDraft(saved) });
            if (memoryDraft) {
              saveMemory.mutate(memoryDraft, { onSuccess: (saved) => setMemoryDraft(saved) });
            }
            if (tierOrderDraft) {
              saveTierOrder.mutate(tierOrderDraft, {
                onSuccess: (saved) => setTierOrderDraft(saved),
              });
            }
            if (ladderDraft && ladderDomain) {
              // eslint-disable-next-line @typescript-eslint/no-unused-vars -- stripped, not sent
              const { id, updated_at, ...rest } = ladderDomain;
              const input: ChatbotDomainInput = { ...rest, ladder: ladderDraft };
              saveDomain.mutate(
                { id: ladderDomain.id, input },
                { onSuccess: (saved) => setLadderDraft(saved.ladder) },
              );
            }
          }}
        >
          {saving ? <LoaderCircleIcon className="animate-spin" /> : null}
          Save
        </Button>
      </div>

      <AlertDialog open={orderingConfirmOpen} onOpenChange={setOrderingConfirmOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            {/* The title deliberately does not repeat the switch's own label: Radix
                points the dialog's `aria-labelledby` at it, so a title carrying the
                same words would make the switch and the dialog answer to one query. */}
            <AlertDialogTitle>Let the CRM finish every turn?</AlertDialogTitle>
            <AlertDialogDescription>
              Each call to /complete then answers 410, so any lane still switched off has
              nobody left to answer it.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={() => set('chatbot_ordering_enabled', true)}>
              Turn it on
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
