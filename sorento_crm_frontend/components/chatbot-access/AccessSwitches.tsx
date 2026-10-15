'use client';

import { useState } from 'react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Switch } from '@/components/ui/switch';
import type { SwitchGroup, SwitchRow } from '@/lib/chatbot-access/tree';

export interface AccessSwitchesProps {
  groups: SwitchGroup[];
  /** 'contact' shows the changed marker and Reset to role; 'role' never does. */
  mode: 'contact' | 'role';
  onToggle: (rowKey: string) => void;
  onResetRow: (rowKey: string) => void;
  onToggleField: (fieldKey: string) => void;
  /** Without it nothing can be switched ON unless it was ON when the page loaded (undoing this
   * session's switch-off); turning OFF stays. */
  canGrant?: boolean;
  /** Nothing can be switched, either way. */
  readOnly?: boolean;
  /** Row, detail and stamp keys that are ON in the saved state. */
  savedOn?: ReadonlySet<string>;
}

function changedText(row: SwitchRow): string {
  const parts: string[] = [];
  if (row.addedCount) parts.push(`+${row.addedCount} details`);
  if (row.removedCount) parts.push(`-${row.removedCount} details`);
  return parts.length
    ? `Changed for this contact: ${parts.join(' ')}`
    : 'Changed for this contact';
}

function Row({
  row,
  mode,
  onToggle,
  onResetRow,
  onToggleField,
  canGrant = true,
  savedOn,
  readOnly = false,
}: { row: SwitchRow } & Omit<AccessSwitchesProps, 'groups'>) {
  const [open, setOpen] = useState(false);
  const shownCount = row.fields.filter((f) => f.on).length;
  const hasDetails = row.fields.length > 0 || !!row.stamp;
  const changed = mode === 'contact' && row.changed;
  const locked = (key: string, on: boolean) => readOnly || !canGrant && !on && !savedOn?.has(key);

  return (
    <div
      className={
        'flex items-start justify-between gap-4 border-t px-5 py-3 first:border-t-0 ' +
        (changed ? 'bg-warning/10' : '')
      }
    >
      <div className="min-w-0 flex-1 space-y-1">
        <div className="font-medium">{row.label}</div>
        {row.needs ? (
          <div className="text-xs text-muted-foreground">Needs {row.needs}</div>
        ) : row.description ? (
          <div className="text-xs text-muted-foreground">{row.description}</div>
        ) : null}
        {changed && (
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="warning" appearance="light" size="sm">
              {changedText(row)}
            </Badge>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => onResetRow(row.key)}
            >
              Reset to role
            </Button>
          </div>
        )}
        {hasDetails && (
          <div>
            <Button
              variant="ghost"
              size="sm"
              aria-expanded={open}
              onClick={() => setOpen((v) => !v)}
            >
              {row.fields.length > 0
                ? `Details shown: ${shownCount} of ${row.fields.length}`
                : 'Pick list options'}
            </Button>
          </div>
        )}
        {open && (
          <div className="space-y-3 pt-1">
            {row.fields.length > 0 && (
              <div className="grid gap-2 sm:grid-cols-2">
                {row.fields.map((f) => (
                  <label
                    key={f.key}
                    className={
                      'flex items-center gap-2 text-sm ' +
                      (mode === 'contact' && f.changed ? 'text-warning' : '')
                    }
                  >
                    <Checkbox
                      checked={f.on}
                      disabled={locked(f.key, f.on)}
                      aria-label={f.label}
                      onCheckedChange={() => onToggleField(f.key)}
                    />
                    <span>{f.label}</span>
                  </label>
                ))}
              </div>
            )}
            {row.stamp && (
              <div className="space-y-2">
                <div className="flex items-center gap-3">
                  <Switch
                    checked={row.stamp.on}
                    disabled={locked(row.stamp.key, row.stamp.on)}
                    aria-label={row.stamp.label}
                    onCheckedChange={() => onToggleField(row.stamp!.key)}
                  />
                  <span className="text-sm">{row.stamp.label}</span>
                </div>
                <div className="grid gap-2 sm:grid-cols-2">
                  <div>
                    <div className="text-xs font-medium text-muted-foreground">
                      On: they see
                    </div>
                    <pre className="whitespace-pre-wrap rounded-md border bg-muted/40 p-2 text-xs">
                      {row.stamp.previewOn}
                    </pre>
                  </div>
                  <div>
                    <div className="text-xs font-medium text-muted-foreground">
                      Off: they see
                    </div>
                    <pre className="whitespace-pre-wrap rounded-md border bg-muted/40 p-2 text-xs">
                      {row.stamp.previewOff}
                    </pre>
                  </div>
                </div>
              </div>
            )}
          </div>
        )}
      </div>
      <Switch
        checked={row.on}
        aria-label={row.label}
        disabled={locked(row.key, row.on)}
        onCheckedChange={() => onToggle(row.key)}
      />
    </div>
  );
}

export default function AccessSwitches({
  groups,
  ...rest
}: AccessSwitchesProps) {
  return (
    <div>
      {groups.map((g) => (
        <section key={g.label}>
          <h3 className="border-t bg-muted/40 px-5 py-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground first:border-t-0">
            {g.label}
          </h3>
          {g.rows.map((row) => (
            <Row key={row.key} row={row} {...rest} />
          ))}
        </section>
      ))}
    </div>
  );
}
