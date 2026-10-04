'use client';

import { useEffect, useState } from 'react';
import { LoaderCircleIcon, Plus, X } from 'lucide-react';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import RecordNavigation from '@/components/common/RecordNavigation';
import {
  NARROWING_POLICY_OPTIONS,
  type NarrowingPolicy,
} from '@/app/(protected)/system-management/chatbot-domains/types/chatbotDomain.types';
import { useCreateChatbotEntityKind, useUpdateChatbotEntityKind } from '../hooks/useChatbotEntityKinds';
import type { ChatbotEntityKind, ChatbotEntityKindInput } from '../types/chatbotEntityKind.types';

/** The range the API accepts (`ChatbotEntityKindBody.roster_cap`, `ge=2, le=50`).
 * Checked here so an out-of-range value reads as inline feedback on the field instead
 * of a raw 422 toast: `min`/`max` on a number input are advisory only, because Save is
 * a click handler and not an HTML form submit. */
const ROSTER_CAP_MIN = 2;
const ROSTER_CAP_MAX = 50;

const BLANK: ChatbotEntityKindInput = {
  code: '',
  label: '',
  resolved_against: '',
  did_you_mean: true,
  default_narrowing: 'optional_filter',
  family_grouping: null,
  base_property_words: {},
  roster_cap: ROSTER_CAP_MAX,
};

export interface ChatbotEntityKindModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Null = create. */
  entityKindCode: string | null;
  rows: ChatbotEntityKind[];
  onNavigate: (code: string) => void;
  /** `system.chatbot_config.manage`. Without it the modal reads the row and nothing more:
   * no Save, every field disabled. The backend enforces the same slug. */
  canManage: boolean;
}

export default function ChatbotEntityKindModal({
  open,
  onOpenChange,
  entityKindCode,
  rows,
  onNavigate,
  canManage,
}: ChatbotEntityKindModalProps) {
  const isNew = entityKindCode === null;
  const current = entityKindCode ? rows.find((r) => r.code === entityKindCode) : null;

  const [draft, setDraft] = useState<ChatbotEntityKindInput>(BLANK);

  useEffect(() => {
    if (!open) return;
    setDraft(current ? { ...current } : BLANK);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, entityKindCode]);

  const set = <K extends keyof ChatbotEntityKindInput>(
    key: K,
    value: ChatbotEntityKindInput[K],
  ) => setDraft((prev) => ({ ...prev, [key]: value }));

  const create = useCreateChatbotEntityKind();
  const update = useUpdateChatbotEntityKind();
  const saving = create.isPending || update.isPending;

  const modalIndex = entityKindCode ? rows.findIndex((r) => r.code === entityKindCode) : -1;

  const rosterCapInvalid =
    draft.roster_cap === undefined ||
    !Number.isInteger(draft.roster_cap) ||
    draft.roster_cap < ROSTER_CAP_MIN ||
    draft.roster_cap > ROSTER_CAP_MAX;

  const handleSave = () => {
    if (!draft.code.trim() || !draft.label.trim() || rosterCapInvalid) return;
    if (isNew) {
      create.mutate(draft, { onSuccess: () => onOpenChange(false) });
    } else if (entityKindCode) {
      update.mutate({ code: entityKindCode, input: draft }, { onSuccess: () => onOpenChange(false) });
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[90dvh] max-w-lg flex-col overflow-hidden p-0">
        <DialogHeader className="flex-row items-center justify-between gap-4 border-b px-6 py-4 pr-12">
          <div className="min-w-0 flex-1">
            <p className="text-xs text-muted-foreground">Entity kinds</p>
            <DialogTitle className="truncate">
              {isNew ? 'Add kind' : (current?.label ?? 'Entity kind')}
            </DialogTitle>
          </div>
          {!isNew && rows.length > 1 && (
            <RecordNavigation
              index={modalIndex + 1}
              total={rows.length}
              hasPrevious={modalIndex > 0}
              hasNext={modalIndex >= 0 && modalIndex < rows.length - 1}
              onPrevious={() => onNavigate(rows[modalIndex - 1].code)}
              onNext={() => onNavigate(rows[modalIndex + 1].code)}
              ariaLabel="entity kind"
            />
          )}
        </DialogHeader>

        <fieldset disabled={!canManage} className="m-0 flex min-w-0 flex-1 flex-col space-y-4 overflow-y-auto border-0 px-6 py-4">
          <div className="space-y-1.5">
            <Label htmlFor="kind-code">Kind</Label>
            <Input
              id="kind-code"
              value={draft.code}
              onChange={(e) => set('code', e.target.value)}
              placeholder="product"
              disabled={!isNew}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="kind-label">Label</Label>
            <Input
              id="kind-label"
              value={draft.label}
              onChange={(e) => set('label', e.target.value)}
              placeholder="Product"
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="kind-resolved-against">Resolved against</Label>
            <Input
              id="kind-resolved-against"
              value={draft.resolved_against}
              onChange={(e) => set('resolved_against', e.target.value)}
              placeholder="Master products (code, name, set members)"
            />
          </div>
          <div className="flex items-center justify-between gap-4">
            <Label htmlFor="kind-did-you-mean" className="cursor-pointer font-normal">
              Did-you-mean
            </Label>
            <Switch
              id="kind-did-you-mean"
              checked={draft.did_you_mean}
              onCheckedChange={(v) => set('did_you_mean', v === true)}
            />
          </div>
          <div className="space-y-1.5">
            <Label>Default narrowing</Label>
            <SearchableSelect
              value={draft.default_narrowing}
              onChange={(v) => set('default_narrowing', v as NarrowingPolicy)}
              options={NARROWING_POLICY_OPTIONS}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="kind-family-grouping">Family grouping</Label>
            <Input
              id="kind-family-grouping"
              value={draft.family_grouping ?? ''}
              onChange={(e) => set('family_grouping', e.target.value || null)}
              placeholder="by base code"
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="kind-roster-cap">Roster cap</Label>
            <Input
              id="kind-roster-cap"
              type="number"
              min={ROSTER_CAP_MIN}
              max={ROSTER_CAP_MAX}
              aria-invalid={rosterCapInvalid}
              value={draft.roster_cap ?? ''}
              onChange={(e) =>
                set('roster_cap', e.target.value === '' ? undefined : Number(e.target.value))
              }
            />
            {rosterCapInvalid && (
              <p className="text-xs text-destructive">
                Enter a whole number between {ROSTER_CAP_MIN} and {ROSTER_CAP_MAX}.
              </p>
            )}
          </div>
          <div className="space-y-1.5">
            <Label>Base property words</Label>
            <p className="text-xs text-muted-foreground">
              A word the parser reads as asking about a property, not a domain, mapped to
              the products column it answers from.
            </p>
            <BasePropertyWordsEditor
              value={draft.base_property_words}
              onChange={(v) => set('base_property_words', v)}
            />
          </div>
        </fieldset>

        <div className="flex items-center justify-end gap-2 border-t px-6 py-4">
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
            {canManage ? 'Cancel' : 'Close'}
          </Button>
          {canManage && (
            <Button
              type="button"
              onClick={handleSave}
              disabled={saving || !draft.code.trim() || !draft.label.trim() || rosterCapInvalid}
            >
              {saving && <LoaderCircleIcon className="size-4 animate-spin" />}
              Save
            </Button>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}

/** Word -> `products` column, e.g. `{ discontinued: "is_discontinued" }`. Simple enough
 * (product-only, a handful of rows) that a dedicated shared component is not worth the
 * indirection for its one caller. */
function BasePropertyWordsEditor({
  value,
  onChange,
}: {
  value: Record<string, string>;
  onChange: (next: Record<string, string>) => void;
}) {
  const entries = Object.entries(value);

  const setEntry = (index: number, key: 'word' | 'column', next: string) => {
    const rows = entries.map(([word, column]) => ({ word, column }));
    rows[index] = { ...rows[index], [key]: next };
    onChange(Object.fromEntries(rows.map((r) => [r.word, r.column])));
  };

  const removeEntry = (index: number) => {
    const rows = entries.filter((_, i) => i !== index);
    onChange(Object.fromEntries(rows));
  };

  const addEntry = () => {
    // A blank key would collide with the next blank row added before it is named -
    // spacer keys keep every row addressable until the reader types a real word.
    let key = '';
    let n = 0;
    do {
      key = n === 0 ? '' : `_new_${n}`;
      n += 1;
    } while (key in value);
    onChange({ ...value, [key]: '' });
  };

  return (
    <div className="space-y-2">
      {entries.length === 0 ? (
        <p className="text-sm text-muted-foreground">No base property words yet.</p>
      ) : (
        entries.map(([word, column], index) => (
          <div key={index} className="flex items-center gap-2">
            <Input
              value={word}
              onChange={(e) => setEntry(index, 'word', e.target.value)}
              placeholder="discontinued"
              aria-label="Word"
              className="flex-1"
            />
            <span className="text-xs text-muted-foreground">-&gt;</span>
            <Input
              value={column}
              onChange={(e) => setEntry(index, 'column', e.target.value)}
              placeholder="is_discontinued"
              aria-label="Products column"
              className="flex-1"
            />
            <Button
              type="button"
              variant="ghost"
              size="icon"
              aria-label={`Remove ${word || 'row'}`}
              onClick={() => removeEntry(index)}
            >
              <X className="size-4" />
            </Button>
          </div>
        ))
      )}
      <Button type="button" variant="outline" size="sm" onClick={addEntry}>
        <Plus className="size-4" />
        Add word
      </Button>
    </div>
  );
}
