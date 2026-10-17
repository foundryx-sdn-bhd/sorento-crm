'use client';

import { useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Filter, Plus, Trash2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Label } from '@/components/ui/label';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { Input } from '@/components/ui/input';
import { NumberInput } from '@/components/ui/number-input';
import { ScrollArea } from '@/components/ui/scroll-area';
import { toast } from '@/lib/toast';
import { numberFieldError } from '@/lib/number-field';
import { OrderFilterFieldSelect } from '@/components/list/OrderFilterFieldSelect';
import {
  fetchListQueryFields,
  type ListQueryFieldMeta,
  type ListQueryFilterGroup,
  type ListQueryResourceKey,
} from '@/lib/list-query/listQueryService';

const OP_LABELS: Record<string, string> = {
  eq: 'Equals',
  ne: 'Not equals',
  contains: 'Contains',
  starts_with: 'Starts with',
  in: 'In list',
  gt: 'Greater than',
  gte: 'Greater or equal',
  lt: 'Less than',
  lte: 'Less or equal',
  is_null: 'Is null / not null',
};

type Row = { id: string; field_key: string; op: string; valueRaw: string };

function parseValue(field: ListQueryFieldMeta | undefined, op: string, raw: string): unknown {
  if (!field) return raw;
  if (op === 'is_null') {
    return raw === 'true';
  }
  if (op === 'in') {
    return raw
      .split(',')
      .map((s) => s.trim())
      .filter(Boolean);
  }
  if (field.data_type === 'number') {
    const n = Number(raw);
    return Number.isFinite(n) ? n : raw;
  }
  if (field.data_type === 'boolean') {
    return raw === 'true';
  }
  return raw === '' ? undefined : raw;
}

function buildGroup(rootOp: 'and' | 'or', rows: Row[], fields: ListQueryFieldMeta[]): ListQueryFilterGroup {
  const byKey = Object.fromEntries(fields.map((f) => [f.field_key, f]));
  const children = rows.map((r) => {
    const f = byKey[r.field_key];
    return {
      field_key: r.field_key,
      op: r.op,
      value: parseValue(f, r.op, r.valueRaw),
    };
  });
  return { op: rootOp, children };
}

function rowsFromFilter(
  filter: ListQueryFilterGroup | null,
  defaultField: ListQueryFieldMeta | undefined,
): { rootOp: 'and' | 'or'; rows: Row[] } {
  if (!filter || !filter.children?.length) {
    return {
      rootOp: 'and',
      rows: defaultField
        ? [
            {
              id: crypto.randomUUID(),
              field_key: defaultField.field_key,
              op: defaultField.allowed_operators[0] || 'eq',
              valueRaw: '',
            },
          ]
        : [],
    };
  }
  const flat = filter.children.filter(
    (c): c is { field_key: string; op: string; value?: unknown } => 'field_key' in c,
  );
  return {
    rootOp: filter.op,
    rows: flat.map((c) => ({
      id: crypto.randomUUID(),
      field_key: c.field_key,
      op: c.op,
      valueRaw:
        c.value === undefined || c.value === null
          ? ''
          : Array.isArray(c.value)
            ? c.value.join(', ')
            : String(c.value),
    })),
  };
}

export type ListQueryFilterDialogProps = {
  resourceKey: ListQueryResourceKey;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  initialFilter: ListQueryFilterGroup | null;
  onApply: (filter: ListQueryFilterGroup | null) => void;
  /** When set, workflow submission list includes form-field filters from the published schema. */
  workflowDefinitionId?: string | null;
};

export function ListQueryFilterDialog({
  resourceKey,
  open,
  onOpenChange,
  initialFilter,
  onApply,
  workflowDefinitionId,
}: ListQueryFilterDialogProps) {
  const { data: fields = [], isLoading } = useQuery({
    queryKey: ['list-query-fields', resourceKey, workflowDefinitionId ?? ''],
    queryFn: () =>
      fetchListQueryFields(resourceKey, {
        definitionId:
          resourceKey === 'workflow_form_submissions' ? workflowDefinitionId ?? undefined : undefined,
      }),
    enabled: open,
    staleTime: 60_000,
  });

  const filterable = useMemo(() => fields.filter((f) => f.filterable), [fields]);
  const defaultField = filterable[0];

  const [rootOp, setRootOp] = useState<'and' | 'or'>('and');
  const [rows, setRows] = useState<Row[]>([]);
  // Set by a refused Apply; from then on a number box shows its sentence live.
  const [applyTried, setApplyTried] = useState(false);

  useEffect(() => {
    if (!open) return;
    const { rootOp: ro, rows: r } = rowsFromFilter(initialFilter, defaultField);
    setRootOp(ro);
    setRows(r.length ? r : []);
  }, [open, initialFilter, defaultField]);

  const addRow = () => {
    if (!defaultField) return;
    setRows((prev) => [
      ...prev,
      {
        id: crypto.randomUUID(),
        field_key: defaultField.field_key,
        op: defaultField.allowed_operators[0] || 'eq',
        valueRaw: '',
      },
    ]);
  };

  const removeRow = (id: string) => setRows((prev) => prev.filter((x) => x.id !== id));

  const updateRow = (id: string, patch: Partial<Row>) =>
    setRows((prev) => prev.map((r) => (r.id === id ? { ...r, ...patch } : r)));

  const handleApply = () => {
    if (!rows.length) {
      onApply(null);
      onOpenChange(false);
      return;
    }
    for (const r of rows) {
      const f = filterable.find((x) => x.field_key === r.field_key);
      if (!f) continue;
      if (r.op !== 'is_null' && r.op !== 'in' && (r.valueRaw === '' || r.valueRaw === undefined)) {
        toast.error(`Enter a value for ${f.label} (${r.op})`);
        return;
      }
      if (f.data_type === 'number' && r.op !== 'is_null' && r.op !== 'in') {
        const problem = numberFieldError(r.valueRaw);
        if (problem) {
          setApplyTried(true);
          toast.error(problem);
          return;
        }
      }
      if (r.op === 'in' && !r.valueRaw.trim()) {
        toast.error(`Enter comma-separated values for ${f.label}`);
        return;
      }
    }
    onApply(buildGroup(rootOp, rows, filterable));
    onOpenChange(false);
  };

  const handleClear = () => {
    onApply(null);
    onOpenChange(false);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Filter className="size-4" />
            Advanced filters
          </DialogTitle>
        </DialogHeader>

        {isLoading ? (
          <p className="text-sm text-muted-foreground">Loading fields…</p>
        ) : !filterable.length ? (
          <p className="text-sm text-muted-foreground">No filterable fields for this list.</p>
        ) : (
          <>
            {resourceKey === 'workflow_form_submissions' && !workflowDefinitionId ? (
              <p className="text-sm text-muted-foreground rounded-md border border-border bg-muted/40 px-3 py-2">
                Select one workflow form in the list toolbar to include filters on form field values (header &
                line items). Submission metadata filters work without a form selection.
              </p>
            ) : null}
            <div className="flex items-center gap-2">
              <Label className="shrink-0">Match</Label>
              <SearchableSelect
                value={rootOp}
                onChange={(v) => setRootOp(v as 'and' | 'or')}
                options={[
                  { value: 'and', label: 'All conditions (AND)' },
                  { value: 'or', label: 'Any condition (OR)' },
                ]}
                triggerClassName="w-[180px]"
              />
            </div>

            <ScrollArea className="max-h-[min(360px,50vh)] pr-3">
              <div className="space-y-3">
                {rows.map((row) => {
                  const f = filterable.find((x) => x.field_key === row.field_key) ?? defaultField!;
                  const ops = f.allowed_operators?.length ? f.allowed_operators : ['eq'];
                  return (
                    <div key={row.id} className="flex flex-col gap-2 rounded-md border p-3 sm:flex-row sm:items-end">
                      <div className="grid flex-1 gap-2 sm:grid-cols-3">
                        <div className="space-y-1">
                          <Label className="text-xs">Field</Label>
                          {resourceKey === 'orders' ? (
                            <OrderFilterFieldSelect
                              value={row.field_key}
                              filterable={filterable}
                              onFieldChange={(v) => {
                                const nf = filterable.find((x) => x.field_key === v)!;
                                updateRow(row.id, {
                                  field_key: v,
                                  op: nf.allowed_operators[0] || 'eq',
                                  valueRaw: '',
                                });
                              }}
                            />
                          ) : (
                            <SearchableSelect
                              value={row.field_key}
                              onChange={(v) => {
                                const nf = filterable.find((x) => x.field_key === v)!;
                                updateRow(row.id, {
                                  field_key: v,
                                  op: nf.allowed_operators[0] || 'eq',
                                  valueRaw: '',
                                });
                              }}
                              options={filterable.map((fld) => ({
                                value: fld.field_key,
                                label: fld.label,
                              }))}
                            />
                          )}
                        </div>
                        <div className="space-y-1">
                          <Label className="text-xs">Operator</Label>
                          <SearchableSelect
                            value={row.op}
                            onChange={(v) => updateRow(row.id, { op: v })}
                            options={ops.map((op) => ({ value: op, label: OP_LABELS[op] || op }))}
                          />
                        </div>
                        <div className="space-y-1">
                          <Label className="text-xs">Value</Label>
                          {row.op === 'is_null' ? (
                            <SearchableSelect
                              value={row.valueRaw || 'true'}
                              onChange={(v) => updateRow(row.id, { valueRaw: v })}
                              options={[
                                { value: 'true', label: 'Is null' },
                                { value: 'false', label: 'Is not null' },
                              ]}
                            />
                          ) : f.data_type === 'boolean' && row.op === 'eq' ? (
                            <SearchableSelect
                              value={row.valueRaw || 'true'}
                              onChange={(v) => updateRow(row.id, { valueRaw: v })}
                              options={[
                                { value: 'true', label: 'True' },
                                { value: 'false', label: 'False' },
                              ]}
                            />
                          ) : f.data_type === 'number' ? (
                            <NumberInput
                              placeholder={row.op === 'in' ? 'a, b, c' : 'Value'}
                              value={row.valueRaw}
                              error={
                                applyTried && row.op !== 'in' && row.op !== 'is_null'
                                  ? numberFieldError(row.valueRaw)
                                  : undefined
                              }
                              onChange={(e) => updateRow(row.id, { valueRaw: e.target.value })}
                            />
                          ) : (
                            <Input
                              placeholder={row.op === 'in' ? 'a, b, c' : 'Value'}
                              value={row.valueRaw}
                              onChange={(e) => updateRow(row.id, { valueRaw: e.target.value })}
                              type={f.data_type === 'date' ? 'date' : 'text'}
                            />
                          )}
                        </div>
                      </div>
                      <Button
                        type="button"
                        mode="icon"
                        variant="ghost"
                        className="text-destructive shrink-0"
                        onClick={() => removeRow(row.id)}
                        aria-label="Remove condition"
                      >
                        <Trash2 className="size-4" />
                      </Button>
                    </div>
                  );
                })}
              </div>
            </ScrollArea>

            <Button type="button" variant="outline" size="sm" className="w-full" onClick={addRow}>
              <Plus className="size-4" />
              Add condition
            </Button>
          </>
        )}

        <DialogFooter className="gap-2 sm:gap-0">
          <Button type="button" variant="ghost" onClick={handleClear}>
            Clear filters
          </Button>
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button type="button" onClick={handleApply} disabled={isLoading || !filterable.length}>
            Apply
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
