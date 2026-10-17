'use client';

import { useRef, useState } from 'react';
import Link from 'next/link';
import {
  AlertTriangle,
  ArrowLeft,
  Check,
  ChevronDown,
  ChevronUp,
  FileText,
  Layers,
  Loader2,
  Pencil,
  Plus,
  Trash2,
  X,
} from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Checkbox } from '@/components/ui/checkbox';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { NumberInput } from '@/components/ui/number-input';
import { numberFieldError, parseNumberDraft } from '@/lib/number-field';
import { Skeleton } from '@/components/ui/skeleton';
import DetailActions from '@/components/common/DetailActions';
import { productSetsPagerQuery } from '../hooks/useProductSets';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { useProductSet, useUpdateProductSet } from '../hooks/useProductSets';
import { getProductSetMemberOptions, type ProductSetMemberOption } from '../services/productSetService';
import type { ProductSetDetail as ProductSetDetailType, ProductSetMember } from '../types/productSet.types';
import { distinctName } from '@/lib/product-display';

const LIST_PATH = '/master-data-management/product-sets';

function money(value: number | null): string {
  return value === null ? '-' : `RM ${value.toLocaleString('en-MY', { minimumFractionDigits: 2 })}`;
}

/** One member row as it is being edited. Position in the array IS the sort
 * order - a separate field for it would just be a second place to disagree
 * with the array the Save button actually sends. */
interface DraftMember {
  /** Stable React key. The real member id when it came off the server,
   * otherwise a locally-minted one for a row that does not exist yet. */
  key: string;
  product_id: string | null;
  product_code: string;
  product_name: string;
  description: string | null;
  list_price: number | null;
  is_discontinued: boolean;
  /** Kept as the Input's own string so a half-typed "1." is not fought.
   * Coerced to a number only when Save builds the payload. */
  quantity: string;
  contributes_to_price: boolean;
  /** Null for a freshly-picked row: stock has never been asked about a member
   * that has not been saved yet, and that is not the same fact as a zero. */
  available: number | null;
}

function toDraft(members: ProductSetMember[]): DraftMember[] {
  return members
    .slice()
    .sort((a, b) => a.sort_order - b.sort_order)
    .map((m) => ({
      key: m.id,
      product_id: m.product_id ?? null,
      product_code: m.product_code,
      product_name: m.product_name,
      description: m.description,
      list_price: m.list_price,
      is_discontinued: m.is_discontinued,
      quantity: String(m.quantity),
      contributes_to_price: m.contributes_to_price,
      available: m.available,
    }));
}

/** The header's price block. View mode: the resolved figure, plus the
 * computed one and who set it when overridden. Edit mode: an input that can
 * set the override AND clear it back to null. */
function PriceBlock({
  set,
  editing,
  overrideDraft,
  onOverrideChange,
  overrideError,
}: {
  set: ProductSetDetailType;
  editing: boolean;
  overrideDraft: string;
  onOverrideChange: (value: string) => void;
  overrideError?: string;
}) {
  const { computed, is_overridden, reason, override_set_by_name } = set.price;

  if (editing) {
    return (
      <div className="min-w-[12rem] space-y-1">
        <Label htmlFor="product-set-override" className="text-xs uppercase tracking-wide text-muted-foreground">
          Price override
        </Label>
        <div className="flex items-center gap-1.5">
          <span className="text-sm text-muted-foreground">RM</span>
          <div>
            <NumberInput
              id="product-set-override"
              min={0}
              step="0.01"
              className="h-8 w-32"
              placeholder={computed === null ? 'No basis' : String(computed)}
              value={overrideDraft}
              onChange={(e) => onOverrideChange(e.target.value)}
              error={overrideError}
            />
          </div>
          {overrideDraft !== '' ? (
            <Button
              mode="icon"
              variant="ghost"
              size="sm"
              title="Clear override"
              aria-label="Clear price override"
              onClick={() => onOverrideChange('')}
            >
              <X className="size-4" />
            </Button>
          ) : null}
        </div>
        <p className="text-xs text-muted-foreground">
          Computed from ticked members: {money(computed)}
        </p>
      </div>
    );
  }

  const { resolved } = set.price;
  if (resolved === null) {
    return (
      <div>
        <div className="text-xs uppercase tracking-wide text-muted-foreground">Price</div>
        <div className="text-sm text-muted-foreground">
          {reason === 'no_members' ? 'No members yet' : 'No member sets the price'}
        </div>
      </div>
    );
  }
  return (
    <div>
      <div className="text-xs uppercase tracking-wide text-muted-foreground">Price</div>
      <div className="flex flex-wrap items-baseline gap-2">
        <span className="text-lg font-semibold tabular-nums">{money(resolved)}</span>
        {is_overridden ? (
          <>
            <Badge variant="warning" size="sm">
              Override
            </Badge>
            <span className="text-xs text-muted-foreground">
              computed {money(computed)}
              {override_set_by_name ? ` · set by ${override_set_by_name}` : ''}
            </span>
          </>
        ) : (
          <span className="text-xs text-muted-foreground">computed from members</span>
        )}
      </div>
    </div>
  );
}

export default function ProductSetDetail({ id }: { id: string }) {
  const { data: set, isLoading, isError, error } = useProductSet(id);
  const update = useUpdateProductSet();

  const [editing, setEditing] = useState(false);
  const [codeDraft, setCodeDraft] = useState('');
  const [nameDraft, setNameDraft] = useState('');
  const [overrideDraft, setOverrideDraft] = useState('');
  const [checked, setChecked] = useState(false);
  const [draftMembers, setDraftMembers] = useState<DraftMember[]>([]);
  const [pickedCode, setPickedCode] = useState('');
  const [pickedOption, setPickedOption] = useState<ProductSetMemberOption | null>(null);
  const nextKeyRef = useRef(0);

  // Records fetched for the prev/next chevrons. There is no server-side
  // neighbours endpoint for sets, and the whole table is small (~94 rows
  // across both companies), so one unfiltered page in the list's own default
  // order is the honest and simplest source - see CertificateDetail for the
  // same shape.

  if (isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  if (isError || !set) {
    return (
      <div className="rounded-lg border border-destructive/40 bg-destructive/5 p-4 text-sm text-destructive">
        {error instanceof Error ? error.message : 'Failed to load this product set.'}
        <div className="pt-3">
          <Button variant="outline" asChild>
            <Link href={LIST_PATH}>
              <ArrowLeft className="size-4" /> Back to product sets
            </Link>
          </Button>
        </div>
      </div>
    );
  }

  function startEdit() {
    if (!set) return;
    setCodeDraft(set.set_code);
    setNameDraft(set.name);
    setOverrideDraft(set.price.override === null ? '' : String(set.price.override));
    setDraftMembers(toDraft(set.members));
    setPickedCode('');
    setPickedOption(null);
    setChecked(false);
    setEditing(true);
  }

  function cancelEdit() {
    setEditing(false);
  }

  // A member quantity has to be a number above zero; the field's min of 0 is only its floor.
  function quantityProblem(raw: string): string | undefined {
    return numberFieldError(raw, { min: 0, minExclusive: true });
  }
  const overrideProblem = numberFieldError(overrideDraft, { min: 0, optional: true });
  const canSave = codeDraft.trim().length > 0 && nameDraft.trim().length > 0 && !update.isPending;

  async function handleSave() {
    if (!set || !canSave) return;
    // Numbers are checked when Save is pressed; afterwards the messages follow the fields.
    setChecked(true);
    if (overrideProblem || draftMembers.some((m) => quantityProblem(m.quantity))) return;
    await update.mutateAsync({
      id: set.id,
      data: {
        set_code: codeDraft.trim(),
        name: nameDraft.trim(),
        list_price_override: parseNumberDraft(overrideDraft),
        members: draftMembers.map((m, idx) => ({
          product_code: m.product_code,
          quantity: parseNumberDraft(m.quantity) as number,
          contributes_to_price: m.contributes_to_price,
          sort_order: idx,
        })),
      },
    });
    setEditing(false);
  }

  function moveMember(index: number, direction: -1 | 1) {
    setDraftMembers((prev) => {
      const target = index + direction;
      if (target < 0 || target >= prev.length) return prev;
      const next = prev.slice();
      [next[index], next[target]] = [next[target], next[index]];
      return next;
    });
  }

  function updateMember(key: string, patch: Partial<DraftMember>) {
    setDraftMembers((prev) => prev.map((m) => (m.key === key ? { ...m, ...patch } : m)));
  }

  function addMember() {
    if (!pickedCode || !pickedOption) return;
    setDraftMembers((prev) => [
      ...prev,
      {
        key: `new-${nextKeyRef.current++}`,
        product_id: pickedOption.product_id,
        product_code: pickedOption.value,
        product_name: pickedOption.product_name,
        description: null,
        list_price: pickedOption.list_price,
        is_discontinued: pickedOption.is_discontinued,
        quantity: '1',
        contributes_to_price: false,
        available: null,
      },
    ]);
    setPickedCode('');
    setPickedOption(null);
  }

  const existingCodes = new Set(draftMembers.map((m) => m.product_code));

  return (
    <div className="space-y-4">
      {/* Read-only metadata lives in the header, never in a section body. */}
      <Card>
        <CardContent className="flex flex-wrap items-start justify-between gap-4 pt-6">
          <div className="min-w-0 flex-1 space-y-1">
            {editing ? (
              <div className="grid max-w-md grid-cols-1 gap-3 sm:grid-cols-2">
                <div className="space-y-1">
                  <Label htmlFor="product-set-code">Set code</Label>
                  <Input
                    id="product-set-code"
                    value={codeDraft}
                    onChange={(e) => setCodeDraft(e.target.value)}
                  />
                </div>
                <div className="space-y-1">
                  <Label htmlFor="product-set-name">Name</Label>
                  <Input
                    id="product-set-name"
                    value={nameDraft}
                    onChange={(e) => setNameDraft(e.target.value)}
                  />
                </div>
              </div>
            ) : (
              <>
                <div className="flex flex-wrap items-center gap-2">
                  <h2 className="truncate text-xl font-semibold" title={set.set_code}>
                    {set.set_code}
                  </h2>
                  <Badge variant={set.is_active ? 'success' : 'secondary'} size="sm">
                    {set.is_active ? 'Active' : 'Inactive'}
                  </Badge>
                </div>
                <p className="truncate text-sm text-muted-foreground" title={set.name}>
                  {set.name}
                </p>
              </>
            )}
            {set.company_name ? (
              <p className="text-xs text-muted-foreground">{set.company_name}</p>
            ) : null}
          </div>

          <div className="flex flex-wrap items-start gap-6">
            <PriceBlock
              set={set}
              editing={editing}
              overrideDraft={overrideDraft}
              overrideError={checked ? overrideProblem : undefined}
              onOverrideChange={setOverrideDraft}
            />
            <div>
              <div className="text-xs uppercase tracking-wide text-muted-foreground">
                Complete sets
              </div>
              {set.complete_sets === null ? (
                <span className="text-sm text-muted-foreground">-</span>
              ) : set.complete_sets === 0 && set.limiting_member_code ? (
                <div className="flex items-center gap-1.5">
                  <span className="text-lg font-semibold tabular-nums text-destructive">0</span>
                  <span className="text-xs text-muted-foreground">
                    short on {set.limiting_member_code}
                  </span>
                </div>
              ) : (
                <span className="text-lg font-semibold tabular-nums">{set.complete_sets}</span>
              )}
            </div>
            {editing ? (
              <div className="flex items-center gap-2">
                <Button variant="outline" onClick={cancelEdit} disabled={update.isPending}>
                  Cancel
                </Button>
                <Button onClick={() => void handleSave()} disabled={!canSave}>
                  {update.isPending ? <Loader2 className="size-4 animate-spin" /> : <Check className="size-4" />}
                  Save product set
                </Button>
              </div>
            ) : (
              <DetailActions
                pager={{
                  ...productSetsPagerQuery,
                  detailPath: LIST_PATH,
                  currentId: set.id,
                  ariaLabel: 'product set',
                }}
                primary={
                  <Button onClick={startEdit}>
                    <Pencil className="size-4" /> Edit
                  </Button>
                }
              />
            )}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between gap-3">
          <CardTitle>Members</CardTitle>
          {!editing ? (
            <Button variant="outline" size="sm" onClick={startEdit}>
              <Plus className="size-4" /> Add member
            </Button>
          ) : null}
        </CardHeader>
        <CardContent>
          {editing ? (
            <div className="flex flex-wrap items-center gap-2 pb-3">
              <SearchableSelect
                value={pickedCode}
                onChange={setPickedCode}
                onOptionChange={(opt) => setPickedOption(opt as ProductSetMemberOption | null)}
                fetchOptions={async (query) => {
                  const rows = await getProductSetMemberOptions(query);
                  return rows.filter((r) => !existingCodes.has(r.value));
                }}
                clearable
                placeholder="Add a product by code"
                emptyMessage="No product left to add."
                size="sm"
                triggerClassName="w-72"
              />
              <Button size="sm" onClick={addMember} disabled={!pickedCode}>
                <Plus className="size-4" /> Add
              </Button>
            </div>
          ) : null}

          {(editing ? draftMembers : set.members).length === 0 ? (
            <div className="flex flex-col items-center gap-3 py-10 text-center">
              <div className="rounded-full bg-muted p-3">
                <Layers className="size-5 text-muted-foreground" />
              </div>
              <p className="max-w-sm text-sm text-muted-foreground">
                This set names no products yet, so it has no price and no stock answer. Add the
                parts it is made of.
              </p>
              {!editing ? (
                <Button size="sm" onClick={startEdit}>
                  <Plus className="size-4" /> Add member
                </Button>
              ) : null}
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[760px] text-sm">
                <thead>
                  <tr className="border-b text-xs uppercase tracking-wide text-muted-foreground">
                    <th className="py-2 pe-3 text-start font-medium">Product</th>
                    <th className="py-2 pe-3 text-start font-medium">List price</th>
                    <th className="py-2 pe-3 text-start font-medium">Qty</th>
                    <th className="py-2 pe-3 text-start font-medium">Sets the price</th>
                    <th className="py-2 pe-3 text-start font-medium">Available</th>
                    {editing ? <th className="py-2 text-end font-medium">Actions</th> : null}
                  </tr>
                </thead>
                <tbody>
                  {(editing ? draftMembers : toDraft(set.members)).map((member, index) => (
                    <tr key={member.key} className="border-b last:border-0">
                      <td className="py-3 pe-3">
                        <div className="flex min-w-0 flex-col">
                          <span className="flex items-center gap-1.5">
                            {member.product_id ? (
                              <Link
                                href={`/master-data-management/products/${member.product_id}`}
                                className="truncate font-medium text-primary hover:underline"
                                title={member.product_code}
                              >
                                {member.product_code}
                              </Link>
                            ) : (
                              <span className="truncate font-medium" title={member.product_code}>
                                {member.product_code}
                              </span>
                            )}
                            {member.is_discontinued ? (
                              <Badge
                                variant="destructive"
                                size="sm"
                                title="Discontinued. The set survives; it cannot complete."
                              >
                                <AlertTriangle className="size-3" /> Discontinued
                              </Badge>
                            ) : null}
                          </span>
                          <span
                            className="truncate text-xs text-muted-foreground"
                            title={member.description ?? distinctName(member.product_code, member.product_name) ?? ''}
                          >
                            {member.description ??
                              distinctName(member.product_code, member.product_name) ??
                              (member.product_code ? null : 'No description')}
                          </span>
                        </div>
                      </td>
                      <td className="py-3 pe-3 tabular-nums">{money(member.list_price)}</td>
                      <td className="py-3 pe-3">
                        {editing ? (
                          <NumberInput
                            min={0}
                            step="0.01"
                            className="h-8 w-20"
                            value={member.quantity}
                            aria-label={`Quantity for ${member.product_code}`}
                            onChange={(e) => updateMember(member.key, { quantity: e.target.value })}
                            error={checked ? quantityProblem(member.quantity) : undefined}
                          />
                        ) : (
                          <span className="tabular-nums">{member.quantity}</span>
                        )}
                      </td>
                      <td className="py-3 pe-3">
                        {editing ? (
                          <Checkbox
                            checked={member.contributes_to_price}
                            aria-label={`${member.product_code} sets the price`}
                            onCheckedChange={(checked) =>
                              updateMember(member.key, { contributes_to_price: checked === true })
                            }
                          />
                        ) : member.contributes_to_price ? (
                          <Badge variant="primary" size="sm">
                            Sets the price
                          </Badge>
                        ) : (
                          <span className="text-xs text-muted-foreground">-</span>
                        )}
                      </td>
                      <td className="py-3 pe-3 tabular-nums">
                        {member.available === null ? (
                          <span className="text-muted-foreground">-</span>
                        ) : member.available === 0 ? (
                          <span className="font-medium text-destructive">0</span>
                        ) : (
                          member.available
                        )}
                      </td>
                      {editing ? (
                        <td className="py-3 text-end">
                          <div className="flex items-center justify-end gap-0.5">
                            <Button
                              mode="icon"
                              variant="ghost"
                              size="sm"
                              title="Move up"
                              aria-label={`Move ${member.product_code} up`}
                              disabled={index === 0}
                              onClick={() => moveMember(index, -1)}
                            >
                              <ChevronUp className="size-4" />
                            </Button>
                            <Button
                              mode="icon"
                              variant="ghost"
                              size="sm"
                              title="Move down"
                              aria-label={`Move ${member.product_code} down`}
                              disabled={index === draftMembers.length - 1}
                              onClick={() => moveMember(index, 1)}
                            >
                              <ChevronDown className="size-4" />
                            </Button>
                            <Button
                              mode="icon"
                              variant="ghost"
                              title="Remove from set"
                              aria-label={`Remove ${member.product_code} from set`}
                              // Nothing is confirmed and nothing is deferred: the
                              // row leaves the DRAFT, not the set. Cancel on the
                              // edit form is the way back, and until Save nothing
                              // has reached the server to take back (D7).
                              onClick={() =>
                                setDraftMembers((prev) =>
                                  prev.filter((m) => m.key !== member.key),
                                )
                              }
                            >
                              <Trash2 className="size-4" />
                            </Button>
                          </div>
                        </td>
                      ) : null}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Linked documents</CardTitle>
        </CardHeader>
        <CardContent>
          {/* No read exists yet that filters attachments/promotions by the set
              that fanned them out (`linked_via_set_id`), so this is an honest
              empty state rather than a promise of behaviour the API cannot
              serve. See the product-sets defect report. */}
          <div className="flex flex-col items-center gap-2 py-6 text-center">
            <div className="rounded-full bg-muted p-3">
              <FileText className="size-5 text-muted-foreground" />
            </div>
            <p className="text-sm text-muted-foreground">
              No attachments or promotions are linked to this set yet.
            </p>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
