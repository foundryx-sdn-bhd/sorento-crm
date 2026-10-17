'use client';

import { useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { toast } from '@/lib/toast';
import { useHasPermission } from '@/hooks/usePermissions';
import { GripVertical, Plus, Trash2, AlertTriangle } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { Sortable, SortableItem, SortableItemHandle } from '@/components/ui/sortable';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { numberFieldError } from '@/lib/number-field';
import { PredicateBuilder } from './PredicateBuilder';
import { isWildcard, type Predicate } from './predicateTypes';
import {
  getRoutingRules,
  getCsCandidates,
  getRoutableFields,
  saveRoutingRules,
  CS_USE_CASES,
  type RoutingRule,
} from './csRoutingMock';

const NO_CS_VALUE = '__no_cs__';

/**
 * R2 - CS-routing predicate rules editor for a contact. Extends the old
 * per-use_case pin table with an ORDERED (drag = priority) list of rules, each
 * with a predicate builder. Highest row = highest priority; a new row appends
 * last. A wildcard (no predicates) ordered above a more-specific row shadows it,
 * so we surface a non-blocking warning.
 *
 * Phase 1: backed by the mock service in `csRoutingMock.ts` (documented contract
 * there); reorder + edits are held in local working state and persisted via the
 * mock `saveRoutingRules`.
 */
let ruleSeq = 1000;
const newRuleId = () => `new-${ruleSeq++}`;

export default function ContactCsRoutingRules({ contactId }: { contactId: string }) {
  // Every routing write needs contacts.edit; without it the rules are read-only.
  const canEdit = useHasPermission('user_management.contacts.edit');
  const { data: fetchedRules, isLoading: rulesLoading } = useQuery({
    queryKey: ['cs-routing-rules', contactId],
    queryFn: () => getRoutingRules(contactId),
    enabled: !!contactId,
  });
  const { data: candidates = [], isLoading: candidatesLoading } = useQuery({
    queryKey: ['cs-routing-candidates'],
    queryFn: getCsCandidates,
    staleTime: 60_000,
  });
  const { data: fields = [] } = useQuery({
    queryKey: ['cs-routing-fields', 'purchase_request'],
    queryFn: () => getRoutableFields('purchase_request'),
    staleTime: 60_000,
  });

  const [rules, setRules] = useState<RoutingRule[]>([]);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (fetchedRules) setRules(fetchedRules);
  }, [fetchedRules]);

  const candidateOptions = useMemo(
    () => [
      { value: NO_CS_VALUE, label: 'No CS (finish at approval)' },
      ...candidates.map((c) => ({ value: c.id, label: c.name, description: c.email })),
    ],
    [candidates],
  );

  const patchRule = (id: string, patch: Partial<RoutingRule>) =>
    setRules((prev) => prev.map((r) => (r.id === id ? { ...r, ...patch } : r)));

  const addRule = () =>
    setRules((prev) => [
      ...prev,
      {
        id: newRuleId(),
        use_case: 'purchase_request',
        cs_pic_user_id: null,
        cs_pic_name: null,
        no_cs: false,
        match_conditions: [],
        priority: prev.length,
      },
    ]);

  const removeRule = (id: string) =>
    setRules((prev) => prev.filter((r) => r.id !== id));

  // Warning: a wildcard row sitting above (earlier than) a more-specific row
  // will always win first and shadow the specific one.
  const wildcardShadowIndex = useMemo(() => {
    for (let i = 0; i < rules.length; i++) {
      if (isWildcard(rules[i].match_conditions)) {
        const hasSpecificBelow = rules
          .slice(i + 1)
          .some((r) => !isWildcard(r.match_conditions));
        if (hasSpecificBelow) return i;
      }
    }
    return -1;
  }, [rules]);

  const handleSave = async () => {
    // A numeric predicate holding bad input shows its sentence under the box; nothing is sent.
    const hasBadNumber = rules.some((r) =>
      r.match_conditions.some(
        (p) =>
          fields.find((f) => f.field === p.field)?.type === 'numeric' &&
          p.value.trim() !== '' &&
          numberFieldError(p.value) !== undefined,
      ),
    );
    if (hasBadNumber) {
      toast.error('Fix the highlighted number before saving');
      return;
    }
    setSaving(true);
    try {
      const saved = await saveRoutingRules(contactId, rules);
      setRules(saved);
      toast.success('Routing rules saved');
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to save routing rules');
    } finally {
      setSaving(false);
    }
  };

  if (rulesLoading || candidatesLoading) {
    return (
      <div className="px-6 pb-6 space-y-3">
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-24 w-full" />
      </div>
    );
  }

  return (
    <div className="px-6 pb-6 space-y-4">
      <p className="text-sm text-muted-foreground">
        Route this salesman&apos;s forms to a specific CS PIC based on the form&apos;s own
        fields. Rules are evaluated top-to-bottom - drag to reorder priority; the first
        matching rule wins. A rule with no conditions is a catch-all.
      </p>

      {candidates.length === 0 ? (
        <div className="rounded-md border border-dashed p-4 text-sm text-muted-foreground">
          No one can process purchase requests yet.
        </div>
      ) : rules.length === 0 ? (
        <div className="rounded-md border border-dashed p-4 text-sm text-muted-foreground">
          No routing rules yet. Add a rule to pin CS assignment by form conditions;
          without any rule the default round-robin applies.
        </div>
      ) : (
        <>
          {wildcardShadowIndex >= 0 ? (
            <div
              role="status"
              className="flex items-start gap-2 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-800"
            >
              <AlertTriangle className="mt-0.5 size-4 shrink-0" />
              <p className="min-w-0">
                A catch-all rule (no conditions) is ordered above a more specific rule - 
                it will match first and shadow the rules below it. Move specific rules
                above catch-alls.
              </p>
            </div>
          ) : null}

          <Sortable
            value={rules}
            onValueChange={setRules}
            getItemValue={(r) => r.id}
            strategy="vertical"
          >
            <fieldset disabled={!canEdit} className="min-w-0 space-y-3">
              {rules.map((rule, index) => (
                <SortableItem key={rule.id} value={rule.id}>
                  <div className="rounded-md border p-3 space-y-3 bg-background">
                    <div className="flex items-center gap-2">
                      {canEdit ? (
                        <SortableItemHandle aria-label="Drag to reorder">
                          <GripVertical className="size-4 text-muted-foreground" />
                        </SortableItemHandle>
                      ) : null}
                      <span className="text-xs font-medium text-muted-foreground">
                        Priority {index + 1}
                      </span>
                      {canEdit ? (
                        <div className="ms-auto flex items-center gap-2">
                          <Button
                            type="button"
                            size="icon"
                            variant="ghost"
                            onClick={() => removeRule(rule.id)}
                            aria-label="Remove rule"
                          >
                            <Trash2 className="size-4" />
                          </Button>
                        </div>
                      ) : null}
                    </div>

                    <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
                      <span className="text-xs text-muted-foreground sm:w-24 shrink-0">
                        Form type
                      </span>
                      <div className="w-full sm:w-56">
                        <SearchableSelect
                          value={rule.use_case}
                          onChange={(v) => v && patchRule(rule.id, { use_case: v })}
                          options={CS_USE_CASES.map((u) => ({ value: u.key, label: u.label }))}
                        />
                      </div>
                    </div>

                    <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
                      <span className="text-xs text-muted-foreground sm:w-24 shrink-0">
                        Assign to
                      </span>
                      <div className="w-full sm:w-72">
                        <SearchableSelect
                          value={rule.no_cs ? NO_CS_VALUE : (rule.cs_pic_user_id ?? '')}
                          onChange={(v) => {
                            const c = candidates.find((x) => x.id === v);
                            patchRule(rule.id, {
                              cs_pic_user_id: v === NO_CS_VALUE ? null : v || null,
                              cs_pic_name: c?.name ?? null,
                              no_cs: v === NO_CS_VALUE,
                            });
                          }}
                          options={candidateOptions}
                          placeholder="Select CS PIC"
                        />
                      </div>
                    </div>

                    <div className="border-t pt-3">
                      <p className="text-xs font-medium mb-2">Conditions</p>
                      <PredicateBuilder
                        predicates={rule.match_conditions}
                        fields={fields}
                        onChange={(next: Predicate[]) =>
                          patchRule(rule.id, { match_conditions: next })
                        }
                        disabled={!canEdit}
                      />
                    </div>
                  </div>
                </SortableItem>
              ))}
            </fieldset>
          </Sortable>
        </>
      )}

      {canEdit ? (
        <div className="flex flex-wrap items-center gap-2">
          <Button type="button" variant="outline" onClick={addRule} disabled={candidates.length === 0}>
            <Plus className="size-4" /> Add rule
          </Button>
          <Button type="button" onClick={handleSave} disabled={saving || candidates.length === 0}>
            {saving ? 'Saving…' : 'Save routing rules'}
          </Button>
        </div>
      ) : null}
    </div>
  );
}
