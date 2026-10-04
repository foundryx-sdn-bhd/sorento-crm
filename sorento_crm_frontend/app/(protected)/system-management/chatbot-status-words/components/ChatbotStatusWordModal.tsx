'use client';

import { useEffect, useState } from 'react';
import { LoaderCircleIcon } from 'lucide-react';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { SearchableMultiSelect } from '@/components/common/SearchableMultiSelect';
import StringChipInput from '@/components/common/StringChipInput';
import RecordNavigation from '@/components/common/RecordNavigation';
import {
  useChatbotStatusWordDeletion,
  useCreateChatbotStatusWord,
  useUpdateChatbotStatusWord,
} from '../hooks/useChatbotStatusWords';
import type { ChatbotStatusWord, ChatbotStatusWordInput } from '../types/chatbotStatusWord.types';

const BLANK: ChatbotStatusWordInput = {
  domain: '',
  value: '',
  label: '',
  trigger_words: [],
  sort_order: 0,
  prompt_lists: [],
};

/** The parser prompt lists a status row can be in (backend `STATUS_PROMPT_LISTS`). */
const PROMPT_LIST_OPTIONS = [
  { value: 'statuses', label: 'Status bullets' },
  { value: 'status_values', label: 'order_status field values' },
  { value: 'status_field_values', label: 'status field values' },
];

const VALUE_PATTERN = /^[a-z][a-z0-9_]*$/;

export interface ChatbotStatusWordModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Null = create. */
  statusId: string | null;
  rows: ChatbotStatusWord[];
  domainOptions: { value: string; label: string }[];
  onNavigate: (id: string) => void;
  canManage: boolean;
}

export default function ChatbotStatusWordModal({
  open,
  onOpenChange,
  statusId,
  rows,
  domainOptions,
  onNavigate,
  canManage,
}: ChatbotStatusWordModalProps) {
  const isNew = statusId === null;
  const current = statusId ? rows.find((r) => r.id === statusId) : null;
  const [draft, setDraft] = useState<ChatbotStatusWordInput>(BLANK);

  useEffect(() => {
    if (!open) return;
    setDraft(
      current
        ? {
            domain: current.domain,
            value: current.value,
            label: current.label,
            trigger_words: current.trigger_words,
            sort_order: current.sort_order,
            prompt_lists: current.prompt_lists ?? [],
          }
        : { ...BLANK, sort_order: rows.length },
    );
    // Re-derive only when the modal opens on a different record.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, statusId]);

  const set = <K extends keyof ChatbotStatusWordInput>(key: K, value: ChatbotStatusWordInput[K]) =>
    setDraft((prev) => ({ ...prev, [key]: value }));

  const create = useCreateChatbotStatusWord();
  const update = useUpdateChatbotStatusWord();
  const deletion = useChatbotStatusWordDeletion();
  const saving = create.isPending || update.isPending;
  const index = statusId ? rows.findIndex((r) => r.id === statusId) : -1;
  const valueValid = VALUE_PATTERN.test(draft.value);
  const canSave = Boolean(draft.domain && valueValid && draft.label.trim()) && !saving;

  const handleSave = () => {
    if (!canSave) return;
    if (isNew) {
      create.mutate(draft, { onSuccess: () => onOpenChange(false) });
    } else if (statusId) {
      update.mutate({ id: statusId, input: draft }, { onSuccess: () => onOpenChange(false) });
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[90dvh] max-w-xl flex-col overflow-hidden p-0">
        <DialogHeader className="flex-row items-center justify-between gap-4 border-b px-6 py-4 pr-12">
          <div className="min-w-0 flex-1">
            <p className="text-xs text-muted-foreground">Chatbot Status Words</p>
            <DialogTitle className="truncate">
              {isNew ? 'Add status word' : (current?.value ?? 'Status word')}
            </DialogTitle>
          </div>
          {!isNew && rows.length > 1 && (
            <RecordNavigation
              index={index + 1}
              total={rows.length}
              hasPrevious={index > 0}
              hasNext={index >= 0 && index < rows.length - 1}
              onPrevious={() => onNavigate(rows[index - 1].id)}
              onNext={() => onNavigate(rows[index + 1].id)}
              ariaLabel="status word"
            />
          )}
        </DialogHeader>

        <div className="flex-1 overflow-y-auto px-6 py-4">
          <fieldset disabled={!canManage} className="m-0 min-w-0 space-y-4 border-0 p-0">
            <div className="space-y-1.5">
              <Label>Domain</Label>
              <SearchableSelect
                value={draft.domain}
                onChange={(v) => set('domain', v)}
                placeholder="Select a domain"
                options={domainOptions}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="status-value">Status value</Label>
              <Input
                id="status-value"
                value={draft.value}
                onChange={(e) => set('value', e.target.value.trim().toLowerCase())}
                placeholder="sales_report"
                aria-invalid={draft.value !== '' && !valueValid}
              />
              {draft.value !== '' && !valueValid ? (
                <p className="text-xs text-destructive">Lowercase letters, digits and underscores.</p>
              ) : null}
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="status-label">Meaning</Label>
              <Input
                id="status-label"
                value={draft.label}
                onChange={(e) => set('label', e.target.value)}
                placeholder="a customer's sales figures by month"
              />
            </div>
            <div className="space-y-1.5">
              <Label>Customer words</Label>
              <StringChipInput
                value={draft.trigger_words}
                onChange={(v) => set('trigger_words', v)}
                placeholder="sales report, laporan jualan"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="status-prompt-lists">Parser prompt lists</Label>
              <SearchableMultiSelect
                id="status-prompt-lists"
                value={draft.prompt_lists}
                onChange={(v) => set('prompt_lists', v)}
                options={PROMPT_LIST_OPTIONS}
                placeholder="Not in any list"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="status-sort">Order</Label>
              <Input
                id="status-sort"
                type="number"
                value={draft.sort_order}
                onChange={(e) => set('sort_order', Number(e.target.value) || 0)}
                className="w-28"
              />
            </div>
          </fieldset>
        </div>

        <div className="flex items-center justify-between gap-3 border-t px-6 py-4">
          <div>
            {!isNew && canManage && current && (
              <Button
                type="button"
                variant="ghost"
                className="text-destructive"
                onClick={() => {
                  deletion.run({ id: current.id, subject: current.value });
                  onOpenChange(false);
                }}
              >
                Delete
              </Button>
            )}
          </div>
          <div className="flex gap-2">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              {canManage ? 'Cancel' : 'Close'}
            </Button>
            {canManage && (
              <Button type="button" onClick={handleSave} disabled={!canSave}>
                {saving && <LoaderCircleIcon className="size-4 animate-spin" />}
                Save
              </Button>
            )}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
