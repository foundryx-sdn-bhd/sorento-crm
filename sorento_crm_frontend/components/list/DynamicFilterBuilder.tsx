'use client';

import { Plus, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { NumberInput } from '@/components/ui/number-input';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { cn } from '@/lib/utils';
import {
  emptyFilterGroup,
  isFilterGroup,
  MAX_FILTER_GROUP_DEPTH,
  newConditionFor,
  operatorsFor,
  OPERATOR_LABEL,
  type FilterFieldDescriptor,
  type FilterOperator,
} from '@/lib/list-query/dynamicFilter';
import type { ListQueryFilterCondition, ListQueryFilterGroup } from '@/lib/list-query/listQueryService';

/**
 * Field + operator + value rows, AND/OR toggles, fully recursive groups (AC-4.1).
 *
 * Reusable by design: the caller supplies a `FilterFieldDescriptor<TRow>[]` (declared
 * beside its own column defs, per PLAN-scm-reorder-oi-feedback-1sep.md S4) and controls
 * the `ListQueryFilterGroup | null` value the way any other controlled input works.
 * There is no field catalog fetch and no Apply button - every edit calls `onChange`
 * immediately, so a caller filtering client-side (`evaluateFilterGroup`) sees the grid
 * narrow as it types.
 */
export interface DynamicFilterBuilderProps<TRow> {
  fields: FilterFieldDescriptor<TRow>[];
  value: ListQueryFilterGroup | null;
  onChange: (next: ListQueryFilterGroup | null) => void;
}

function ValueInput<TRow>({
  field,
  condition,
  onChange,
}: {
  field: FilterFieldDescriptor<TRow>;
  condition: ListQueryFilterCondition;
  onChange: (value: unknown) => void;
}) {
  if (condition.op === 'is_empty') {
    return (
      <SearchableSelect
        value={condition.value === false ? 'not_empty' : 'empty'}
        onChange={(v) => onChange(v !== 'not_empty')}
        options={[
          { value: 'empty', label: 'Is empty' },
          { value: 'not_empty', label: 'Is not empty' },
        ]}
      />
    );
  }

  if (condition.op === 'between') {
    const bounds = Array.isArray(condition.value) ? condition.value : ['', ''];
    return (
      <div className="flex items-center gap-2">
        <NumberInput
          placeholder="Min"
          value={bounds[0] ?? ''}
          onChange={(e) => onChange([e.target.value, bounds[1] ?? ''])}
        />
        <span className="text-xs text-muted-foreground">and</span>
        <NumberInput
          placeholder="Max"
          value={bounds[1] ?? ''}
          onChange={(e) => onChange([bounds[0] ?? '', e.target.value])}
        />
      </div>
    );
  }

  if (condition.op === 'in') {
    const raw = Array.isArray(condition.value) ? condition.value.join(', ') : '';
    return (
      <Input
        placeholder="a, b, c"
        value={raw}
        onChange={(e) =>
          onChange(
            e.target.value
              .split(',')
              .map((s) => s.trim())
              .filter(Boolean),
          )
        }
      />
    );
  }

  if (field.type === 'select' && condition.op === 'eq') {
    return (
      <SearchableSelect
        value={typeof condition.value === 'string' ? condition.value : ''}
        onChange={(v) => onChange(v)}
        options={field.options ?? []}
        placeholder="Value"
      />
    );
  }

  const singleValue =
    typeof condition.value === 'string' || typeof condition.value === 'number' ? condition.value : '';
  if (field.type === 'number') {
    return (
      <NumberInput
        placeholder="Value"
        value={singleValue}
        onChange={(e) => onChange(e.target.value)}
      />
    );
  }
  return (
    <Input
      type="text"
      placeholder="Value"
      value={singleValue}
      onChange={(e) => onChange(e.target.value)}
    />
  );
}

function ConditionRow<TRow>({
  condition,
  fields,
  onChange,
  onRemove,
}: {
  condition: ListQueryFilterCondition;
  fields: FilterFieldDescriptor<TRow>[];
  onChange: (next: ListQueryFilterCondition) => void;
  onRemove: () => void;
}) {
  const field = fields.find((f) => f.field_key === condition.field_key) ?? fields[0];
  const ops = operatorsFor(field);

  return (
    <div className="space-y-2 rounded-md border border-border p-2">
      <div className="flex items-center justify-between gap-1">
        <SearchableSelect
          value={condition.field_key}
          onChange={(v) => {
            const nf = fields.find((f) => f.field_key === v)!;
            onChange(newConditionFor(nf));
          }}
          options={fields.map((f) => ({ value: f.field_key, label: f.label }))}
          triggerClassName="flex-1"
        />
        <Button
          type="button"
          variant="ghost"
          mode="icon"
          size="sm"
          className="size-7 shrink-0 text-destructive"
          onClick={onRemove}
          aria-label="Remove condition"
        >
          <X className="size-3.5" />
        </Button>
      </div>
      <SearchableSelect
        value={condition.op}
        onChange={(v) =>
          onChange({
            ...condition,
            op: v as FilterOperator,
            value: v === 'is_empty' ? true : undefined,
          })
        }
        options={ops.map((op) => ({ value: op, label: OPERATOR_LABEL[op] }))}
      />
      <ValueInput field={field} condition={condition} onChange={(value) => onChange({ ...condition, value })} />
    </div>
  );
}

function FilterGroupNode<TRow>({
  group,
  fields,
  onChange,
  onRemove,
  depth = 0,
}: {
  group: ListQueryFilterGroup;
  fields: FilterFieldDescriptor<TRow>[];
  onChange: (next: ListQueryFilterGroup) => void;
  onRemove?: () => void;
  depth?: number;
}) {
  const updateChild = (index: number, next: ListQueryFilterGroup | ListQueryFilterCondition) => {
    const children = [...group.children];
    children[index] = next;
    onChange({ ...group, children });
  };
  const removeChild = (index: number) => {
    onChange({ ...group, children: group.children.filter((_, i) => i !== index) });
  };
  const addCondition = () => {
    if (!fields.length) return;
    onChange({ ...group, children: [...group.children, newConditionFor(fields[0])] });
  };
  const addGroup = () => {
    onChange({ ...group, children: [...group.children, emptyFilterGroup('and')] });
  };
  // S6: `depth` is 0-based (root = 0), `MAX_FILTER_GROUP_DEPTH` is root-inclusive
  // (root alone = depth 1, matching the backend validator) - so a new child group
  // stays within the cap only while this node's OWN depth-from-root (`depth + 1`)
  // has room for one more level.
  const canAddGroup = depth + 2 <= MAX_FILTER_GROUP_DEPTH;

  return (
    <div
      data-testid="dynamic-filter-group"
      className={cn('space-y-2', depth > 0 && 'border-l-2 border-border pl-3')}
    >
      <div className="flex items-center gap-2">
        <SearchableSelect
          value={group.op}
          onChange={(v) => onChange({ ...group, op: v as 'and' | 'or' })}
          options={[
            { value: 'and', label: 'All conditions (AND)' },
            { value: 'or', label: 'Any condition (OR)' },
          ]}
        />
        {onRemove ? (
          <Button
            type="button"
            variant="ghost"
            mode="icon"
            size="sm"
            className="size-7 shrink-0 text-destructive"
            onClick={onRemove}
            aria-label="Remove group"
          >
            <X className="size-3.5" />
          </Button>
        ) : null}
      </div>

      <div className="space-y-2">
        {group.children.map((child, index) =>
          isFilterGroup(child) ? (
            <FilterGroupNode
              key={index}
              group={child}
              fields={fields}
              onChange={(next) => updateChild(index, next)}
              onRemove={() => removeChild(index)}
              depth={depth + 1}
            />
          ) : (
            <ConditionRow
              key={index}
              condition={child}
              fields={fields}
              onChange={(next) => updateChild(index, next)}
              onRemove={() => removeChild(index)}
            />
          ),
        )}
      </div>

      <div className="flex gap-2">
        <Button type="button" variant="outline" size="sm" className="gap-1" onClick={addCondition}>
          <Plus className="size-3.5" />
          Condition
        </Button>
        {canAddGroup ? (
          <Button type="button" variant="outline" size="sm" className="gap-1" onClick={addGroup}>
            <Plus className="size-3.5" />
            Group
          </Button>
        ) : null}
      </div>
    </div>
  );
}

export function DynamicFilterBuilder<TRow>({ fields, value, onChange }: DynamicFilterBuilderProps<TRow>) {
  if (!fields.length) {
    return <p className="text-sm text-muted-foreground">No filterable fields for this list.</p>;
  }
  const group = value ?? emptyFilterGroup('and');

  return (
    <div className="space-y-2">
      <FilterGroupNode
        group={group}
        fields={fields}
        onChange={(next) => onChange(next.children.length ? next : null)}
      />
      {group.children.length > 0 ? (
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="w-full text-muted-foreground"
          onClick={() => onChange(null)}
        >
          Clear all filters
        </Button>
      ) : null}
    </div>
  );
}

export default DynamicFilterBuilder;
