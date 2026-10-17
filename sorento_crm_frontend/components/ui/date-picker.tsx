'use client';

import * as React from 'react';
import { CalendarIcon } from 'lucide-react';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { Calendar } from '@/components/ui/calendar';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { Input } from '@/components/ui/input';

/** Format Date to dd/mm/yyyy (local). */
function formatDDMMYYYY(value: Date): string {
  const d = value.getDate();
  const m = value.getMonth() + 1;
  const y = value.getFullYear();
  return `${String(d).padStart(2, '0')}/${String(m).padStart(2, '0')}/${y}`;
}

/** A local Date only when it IS that day: `new Date` rolls day 0 or 31 Feb into another one. */
function realDate(y: number, m: number, d: number): Date | undefined {
  const date = new Date(y, m - 1, d);
  return date.getFullYear() === y &&
    date.getMonth() === m - 1 &&
    date.getDate() === d
    ? date
    : undefined;
}

/** Parse YYYY-MM-DD or DD/MM/YYYY to local Date; return undefined if invalid or empty.
 *  ISO needs both digits of month and day, so a half-typed `2026-07-0` is not a date yet. */
export function parseDateInput(value: string): Date | undefined {
  if (!value || value.trim() === '') return undefined;
  const trimmed = value.trim();
  if (/^\d{4}-\d{2}-\d{2}$/.test(trimmed)) {
    const [y, m, d] = trimmed.split('-').map(Number);
    return realDate(y, m, d);
  }
  if (/^\d{1,2}\/\d{1,2}\/\d{4}$/.test(trimmed)) {
    const [d, m, y] = trimmed.split('/').map(Number);
    return realDate(y, m, d);
  }
  return undefined;
}

export interface DatePickerProps {
  value?: Date;
  onChange?: (date: Date | undefined) => void;
  placeholder?: string;
  disabled?: boolean;
  className?: string;
  /** If true, required field - clearing the input does not call onChange(undefined). */
  required?: boolean;
  /** Forwarded to the typed input, for a table cell's own label/id. */
  id?: string;
  ariaLabel?: string;
  /** Height of the typed input and the calendar-icon button, e.g. for a table cell row. */
  inputClassName?: string;
}

/**
 * Date picker with a typeable input (full 4-digit year) and calendar popover.
 * Displays and accepts DD/MM/YYYY when typing (also accepts YYYY-MM-DD).
 */
export function DatePicker({
  value,
  onChange,
  placeholder = 'DD/MM/YYYY',
  disabled = false,
  className,
  required = false,
  id,
  ariaLabel,
  inputClassName,
}: DatePickerProps) {
  const [open, setOpen] = React.useState(false);
  const [inputValue, setInputValue] = React.useState(() =>
    value ? formatDDMMYYYY(value) : '',
  );

  React.useEffect(() => {
    setInputValue(value ? formatDDMMYYYY(value) : '');
  }, [value]);

  const handleInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const raw = e.target.value;
    setInputValue(raw);
    const parsed = parseDateInput(raw);
    if (parsed !== undefined) {
      onChange?.(parsed);
    } else if (raw.trim() === '' && !required) {
      onChange?.(undefined);
    }
  };

  const handleSelect = (date: Date | undefined) => {
    if (date) {
      setInputValue(formatDDMMYYYY(date));
      onChange?.(date);
      setOpen(false);
    }
  };

  return (
    <div className={cn('flex gap-2', className)}>
      <Input
        id={id}
        type="text"
        placeholder={placeholder}
        value={inputValue}
        onChange={handleInputChange}
        disabled={disabled}
        aria-label={ariaLabel}
        className={cn('flex-1', inputClassName)}
        autoComplete="off"
      />
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <Button
            type="button"
            variant="outline"
            size="icon"
            disabled={disabled}
            className={cn('shrink-0', inputClassName)}
            aria-label={ariaLabel ? `Pick a date for ${ariaLabel}` : 'Pick a date'}
          >
            <CalendarIcon className="h-4 w-4" />
          </Button>
        </PopoverTrigger>
        <PopoverContent className="w-auto p-0" align="start">
          <Calendar
            mode="single"
            selected={value}
            onSelect={handleSelect}
            initialFocus
          />
        </PopoverContent>
      </Popover>
    </div>
  );
}
