'use client';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { NumberInput } from '@/components/ui/number-input';
import { numberFieldError } from '@/lib/number-field';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { Checkbox } from '@/components/ui/checkbox';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { Plus, Trash2 } from 'lucide-react';
import type { WorkflowFormSchema, WorkflowHeaderField } from '../types/workflowForms.types';

/** A required number may not be empty; an optional one is empty or valid. */
function workflowNumberError(f: WorkflowHeaderField, value: unknown): string | undefined {
  return numberFieldError(value as number | string | null | undefined, { optional: !f.required });
}

/** True when any number field in the header or the line rows is empty-but-required or invalid. */
export function workflowNumbersInvalid(
  schema: WorkflowFormSchema,
  header: Record<string, unknown>,
  lines: { line_group_id: string; row_data: Record<string, unknown> }[] = [],
): boolean {
  const headerFields = headerFieldLists(schema).flatMap((g) => g.fields);
  if (headerFields.some((f) => f.type === 'number' && workflowNumberError(f, header[f.id]))) return true;
  return lines.some((l) => {
    const group = schema.line_groups.find((g) => g.id === l.line_group_id);
    return (group?.fields ?? []).some(
      (f) => f.type === 'number' && workflowNumberError(f, l.row_data[f.id]),
    );
  });
}

function renderField(
  f: WorkflowHeaderField,
  value: unknown,
  onChange: (v: unknown) => void,
  disabled: boolean,
  showNumberErrors = false,
) {
  const common = { disabled, placeholder: f.placeholder };
  switch (f.type) {
    case 'textarea':
      return (
        <Textarea
          {...common}
          rows={3}
          value={typeof value === 'string' ? value : ''}
          onChange={(e) => onChange(e.target.value)}
        />
      );
    case 'number':
      return (
        <NumberInput
          {...common}
          value={value === undefined || value === null ? null : (value as number | string)}
          onValueChange={(n) => onChange(n)}
          error={showNumberErrors ? workflowNumberError(f, value) : undefined}
        />
      );
    case 'checkbox':
      return (
        <Checkbox
          disabled={disabled}
          checked={Boolean(value)}
          onCheckedChange={(c) => onChange(c === true)}
        />
      );
    case 'select':
    case 'radio':
      return (
        <SearchableSelect
          disabled={disabled}
          value={typeof value === 'string' ? value : ''}
          onChange={onChange}
          options={(f.options ?? []).map((o) => ({ value: o.value, label: o.label }))}
          placeholder="Select…"
        />
      );
    case 'multi_select': {
      const arr = Array.isArray(value) ? (value as string[]) : [];
      const toggle = (v: string, on: boolean) => {
        if (on) onChange(Array.from(new Set([...arr, v])));
        else onChange(arr.filter((x) => x !== v));
      };
      return (
        <div className="flex flex-col gap-2 border rounded-md p-2">
          {(f.options ?? []).map((o) => (
            <div key={o.value} className="flex items-center gap-2">
              <Checkbox
                disabled={disabled}
                checked={arr.includes(o.value)}
                onCheckedChange={(c) => toggle(o.value, c === true)}
              />
              <span className="text-sm">{o.label}</span>
            </div>
          ))}
        </div>
      );
    }
    case 'date_range': {
      const dr =
        value && typeof value === 'object' && !Array.isArray(value)
          ? (value as { start?: string; end?: string })
          : {};
      const start = typeof dr.start === 'string' ? dr.start : '';
      const end = typeof dr.end === 'string' ? dr.end : '';
      return (
        <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:gap-4">
          <div className="space-y-1 flex-1 min-w-0">
            <Label className="text-xs text-muted-foreground">Start date</Label>
            <Input
              type="date"
              disabled={disabled}
              value={start}
              onChange={(e) => onChange({ start: e.target.value, end })}
            />
          </div>
          <div className="space-y-1 flex-1 min-w-0">
            <Label className="text-xs text-muted-foreground">End date</Label>
            <Input
              type="date"
              disabled={disabled}
              value={end}
              min={start || undefined}
              onChange={(e) => onChange({ start, end: e.target.value })}
            />
          </div>
        </div>
      );
    }
    default:
      return (
        <Input
          {...common}
          type={f.type === 'email' ? 'email' : f.type === 'date' ? 'date' : 'text'}
          value={typeof value === 'string' || typeof value === 'number' ? String(value ?? '') : ''}
          onChange={(e) => onChange(e.target.value)}
        />
      );
  }
}

function headerFieldLists(schema: WorkflowFormSchema): { title: string; fields: WorkflowHeaderField[] }[] {
  if (schema.header_sections?.length) {
    return schema.header_sections.map((s) => ({ title: s.title, fields: s.fields }));
  }
  return [{ title: '', fields: schema.header_fields ?? [] }];
}

export function WorkflowHeaderFieldsForm({
  schema,
  data,
  onChange,
  disabled,
  showNumberErrors,
}: {
  schema: WorkflowFormSchema;
  data: Record<string, unknown>;
  onChange: (next: Record<string, unknown>) => void;
  disabled?: boolean;
  /** Set once a Save found a wrong number; the sentences then follow the typing. */
  showNumberErrors?: boolean;
}) {
  const groups = headerFieldLists(schema);

  return (
    <div className="space-y-8">
      {groups.map((g, gi) => (
        <div key={`hdr-sec-${gi}`} className="space-y-4">
          {g.title ? (
            <h3 className="text-sm font-semibold text-foreground border-b pb-2">{g.title}</h3>
          ) : null}
          {g.fields.map((f, i) => (
            <div key={`hdr-${f.id}-${i}`} className="space-y-1">
              <Label className="flex flex-col gap-0.5 items-start">
                <span>
                  {f.label}
                  {f.required ? ' *' : ''}
                </span>
                {f.help_text ? (
                  <span className="text-xs font-normal text-muted-foreground whitespace-pre-wrap">{f.help_text}</span>
                ) : null}
              </Label>
              {renderField(
                f,
                data[f.id],
                (v) => onChange({ ...data, [f.id]: v }),
                !!disabled,
                showNumberErrors,
              )}
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}

export function WorkflowLineGroupsEditor({
  schema,
  lines,
  onChange,
  disabled,
  showNumberErrors,
}: {
  schema: WorkflowFormSchema;
  lines: { line_group_id: string; sort_order: number; row_data: Record<string, unknown> }[];
  onChange: (next: typeof lines) => void;
  disabled?: boolean;
  showNumberErrors?: boolean;
}) {
  const addRow = (groupId: string) => {
    const row = { line_group_id: groupId, sort_order: lines.filter((l) => l.line_group_id === groupId).length, row_data: {} };
    onChange([...lines, row]);
  };

  const updateRow = (idx: number, row_data: Record<string, unknown>) => {
    const next = [...lines];
    next[idx] = { ...next[idx], row_data };
    onChange(next);
  };

  const removeRow = (idx: number) => {
    onChange(lines.filter((_, i) => i !== idx));
  };

  return (
    <div className="space-y-6">
      {schema.line_groups.map((g, gi) => {
        const rowIndexes = lines
          .map((l, i) => (l.line_group_id === g.id ? i : -1))
          .filter((i) => i >= 0);
        return (
          <div key={`lg-${gi}`} className="border rounded-md p-4 space-y-3">
            <div className="flex justify-between items-center">
              <h4 className="font-medium">{g.name}</h4>
              {!disabled ? (
                <Button type="button" size="sm" variant="outline" onClick={() => addRow(g.id)}>
                  <Plus className="size-4 mr-1" />
                  Add row
                </Button>
              ) : null}
            </div>
            {rowIndexes.length === 0 ? (
              <p className="text-sm text-muted-foreground">No rows.</p>
            ) : (
              rowIndexes.map((idx) => (
                <div key={idx} className="border rounded p-3 space-y-3 relative">
                  {!disabled ? (
                    <Button
                      type="button"
                      size="icon"
                      variant="ghost"
                      className="absolute top-2 right-2"
                      onClick={() => removeRow(idx)}
                      aria-label="Delete row"
                    >
                      <Trash2 className="size-4 text-destructive" />
                    </Button>
                  ) : null}
                  <div className="grid gap-3 md:grid-cols-2 pe-8">
                    {g.fields.map((f, fi) => (
                      <div key={`lg-${gi}-f-${fi}`} className="space-y-1">
                        <Label className="text-xs">
                          {f.label}
                          {f.required ? ' *' : ''}
                        </Label>
                        {renderField(
                          f,
                          lines[idx].row_data[f.id],
                          (v) => updateRow(idx, { ...lines[idx].row_data, [f.id]: v }),
                          !!disabled,
                          showNumberErrors,
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              ))
            )}
          </div>
        );
      })}
    </div>
  );
}
