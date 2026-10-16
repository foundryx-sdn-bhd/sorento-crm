'use client';

import { Plus, Trash2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { NumberInput } from '@/components/ui/number-input';
import { numberFieldError } from '@/lib/number-field';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import {
  type Predicate,
  type PredicateOperator,
  type RoutableField,
  operatorsForFieldType,
  fieldUsesOptionValue,
} from './predicateTypes';

/**
 * Pure predicate editor (R2). Renders the AND-combined list of `{field, operator,
 * value}` rows for one routing rule. Kept dependency-light (no data fetching) so
 * it is unit-testable in isolation.
 *
 * - field dropdown = the form's user-facing routable fields
 * - operator dropdown = equals / not_equals (+ contains / not_contains for string)
 * - value input adapts by field type: lookup/enum → option dropdown, numeric →
 *   number input, string → text input
 * - empty list = wildcard (matches everything)
 */
export interface PredicateBuilderProps {
  predicates: Predicate[];
  fields: RoutableField[];
  onChange: (next: Predicate[]) => void;
  disabled?: boolean;
}

export function PredicateBuilder({
  predicates,
  fields,
  onChange,
  disabled = false,
}: PredicateBuilderProps) {
  const fieldMeta = (name: string) => fields.find((f) => f.field === name);

  const fieldOptions = fields.map((f) => ({ value: f.field, label: f.label }));

  const update = (index: number, patch: Partial<Predicate>) => {
    onChange(predicates.map((p, i) => (i === index ? { ...p, ...patch } : p)));
  };

  const changeField = (index: number, field: string) => {
    // Reset operator + value when the field changes (they're field-dependent).
    const meta = fieldMeta(field);
    const ops = operatorsForFieldType(meta?.type);
    update(index, {
      field,
      operator: (ops[0]?.value ?? 'equals') as PredicateOperator,
      value: '',
    });
  };

  const addPredicate = () => {
    const first = fields[0];
    const ops = operatorsForFieldType(first?.type);
    onChange([
      ...predicates,
      {
        field: first?.field ?? '',
        operator: (ops[0]?.value ?? 'equals') as PredicateOperator,
        value: '',
      },
    ]);
  };

  const removePredicate = (index: number) => {
    onChange(predicates.filter((_, i) => i !== index));
  };

  return (
    <div className="space-y-2">
      {predicates.length === 0 ? (
        <p className="text-xs text-muted-foreground">
          No conditions - this rule matches <span className="font-medium">all</span> forms
          (wildcard).
        </p>
      ) : (
        predicates.map((p, i) => {
          const meta = fieldMeta(p.field);
          const ops = operatorsForFieldType(meta?.type);
          const useOptionValue = fieldUsesOptionValue(meta?.type);
          return (
            <div
              key={i}
              className="flex flex-col gap-2 sm:flex-row sm:items-center"
              data-testid="predicate-row"
            >
              {i > 0 ? (
                <span className="text-xs font-medium text-muted-foreground sm:w-10">and</span>
              ) : (
                <span className="text-xs text-muted-foreground sm:w-10">where</span>
              )}
              <div className="w-full sm:w-44">
                <SearchableSelect
                  value={p.field}
                  onChange={(v) => v && changeField(i, v)}
                  options={fieldOptions}
                  placeholder="Field"
                  disabled={disabled}
                />
              </div>
              <div className="w-full sm:w-40">
                <SearchableSelect
                  value={p.operator}
                  onChange={(v) => v && update(i, { operator: v as PredicateOperator })}
                  options={ops}
                  placeholder="Operator"
                  disabled={disabled}
                />
              </div>
              <div className="w-full sm:flex-1">
                {useOptionValue ? (
                  <SearchableSelect
                    value={p.value}
                    onChange={(v) => update(i, { value: v })}
                    options={(meta?.options ?? []).map((o) => ({
                      value: o.value,
                      label: o.label,
                    }))}
                    placeholder="Value"
                    emptyMessage="No options."
                    disabled={disabled}
                  />
                ) : meta?.type === 'numeric' ? (
                  <NumberInput
                    className="h-9"
                    inputMode="decimal"
                    value={p.value}
                    error={p.value.trim() ? numberFieldError(p.value) : undefined}
                    onChange={(e) => update(i, { value: e.target.value })}
                    placeholder="Value"
                    disabled={disabled}
                    aria-label="Predicate value"
                  />
                ) : (
                  <Input
                    className="h-9"
                    type="text"
                    value={p.value}
                    onChange={(e) => update(i, { value: e.target.value })}
                    placeholder="Value"
                    disabled={disabled}
                    aria-label="Predicate value"
                  />
                )}
              </div>
              <Button
                type="button"
                size="icon"
                variant="ghost"
                onClick={() => removePredicate(i)}
                disabled={disabled}
                aria-label="Remove condition"
              >
                <Trash2 className="size-4" />
              </Button>
            </div>
          );
        })
      )}
      <Button
        type="button"
        size="sm"
        variant="outline"
        onClick={addPredicate}
        disabled={disabled || fields.length === 0}
      >
        <Plus className="size-4" /> Add condition
      </Button>
    </div>
  );
}
