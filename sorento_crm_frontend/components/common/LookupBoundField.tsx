'use client';

import { useEffect, useMemo, useRef } from 'react';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { useLookupOptionsByBinding } from '@/hooks/useLookupOptionsByBinding';

export interface LookupBoundFieldProps {
  table: string;
  column: string;
  value: string | null | undefined;
  onChange: (next: string) => void;
  placeholder?: string;
  renderFallback: () => React.ReactNode;
  /** Show a clear (x) button; set it on an optional field. */
  clearable?: boolean;
}

export default function LookupBoundField({
  table,
  column,
  value,
  onChange,
  placeholder,
  renderFallback,
  clearable = false,
}: LookupBoundFieldProps) {
  const { data, isLoading } = useLookupOptionsByBinding(table, column);

  const hasBinding = !!data?.set_key;
  const options = useMemo(() => data?.options ?? [], [data]);

  // Pre-select the binding's declared default_value - but ONLY on a NEW form
  // (the field started empty). We never override an existing value (edit form),
  // and we fire exactly once so a user who deliberately clears the field is not
  // re-defaulted on the next render. The empty-at-mount check discriminates
  // "new form" from "edit form" (edit resets a concrete value before this runs).
  const appliedDefaultRef = useRef(false);
  const startedEmptyRef = useRef<boolean | null>(null);
  if (startedEmptyRef.current === null) {
    startedEmptyRef.current = !((value ?? '').trim());
  }
  const defaultValue = data?.default_value ?? null;
  useEffect(() => {
    if (appliedDefaultRef.current) return;
    if (!startedEmptyRef.current) return; // edit form - leave the existing value
    if ((value ?? '').trim()) return; // something already set it
    if (!defaultValue) return;
    if (!options.some((o) => o.value.toLowerCase() === defaultValue.toLowerCase())) {
      return; // default not a valid option - ignore defensively
    }
    appliedDefaultRef.current = true;
    onChange(defaultValue);
  }, [defaultValue, options, value, onChange]);

  if (isLoading || !data) return <>{renderFallback()}</>;
  if (!hasBinding) return <>{renderFallback()}</>;

  const current = value ?? '';
  const ciMatch = current
    ? options.find((o) => o.value.toLowerCase() === current.toLowerCase())
    : undefined;
  const displayValue = ciMatch ? ciMatch.value : current || '';
  const showLegacy = !ciMatch && !!current;

  return (
    <SearchableSelect
      key={displayValue || 'empty'}
      value={displayValue}
      onChange={onChange}
      options={[
        ...(showLegacy ? [{ value: current, label: `${current} (legacy)` }] : []),
        ...options.map((o) => ({ value: o.value, label: o.label })),
      ]}
      placeholder={placeholder ?? 'Select…'}
      clearable={clearable}
    />
  );
}
