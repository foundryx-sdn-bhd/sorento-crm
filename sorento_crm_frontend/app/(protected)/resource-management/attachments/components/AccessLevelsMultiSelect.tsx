'use client';

import { ChevronDown, X } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { SelectLoadFailure } from '@/components/common/SelectLoadFailure';
import { Checkbox } from '@/components/ui/checkbox';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';

export type AccessLevelOption = {
  code: string;
  name?: string | null;
};

type Props = {
  options: AccessLevelOption[];
  value: string[];
  onChange: (value: string[]) => void;
  disabled?: boolean;
  loadError?: unknown;
  onRetry?: () => void;
};

export function AccessLevelsMultiSelect({
  options,
  value,
  onChange,
  disabled,
  loadError,
  onRetry,
}: Props) {
  const selected = new Set(value);
  const labelFor = (code: string) => options.find((opt) => opt.code === code)?.name || code;
  const allSelected = options.length > 0 && options.every((opt) => selected.has(opt.code));

  const setOne = (code: string, checked: boolean) => {
    const next = new Set(selected);
    if (checked) {
      next.add(code);
    } else {
      next.delete(code);
    }
    onChange(Array.from(next));
  };

  const toggleAll = () => {
    onChange(allSelected ? [] : options.map((opt) => opt.code));
  };

  return (
    <div className="space-y-2">
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button type="button" variant="outline" className="w-full justify-between" disabled={disabled}>
            <span>{value.length ? `${value.length} selected` : 'Select access levels'}</span>
            <ChevronDown className="size-4" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="w-72">
          {loadError != null && loadError !== false && options.length === 0 ? (
            <SelectLoadFailure error={loadError} onRetry={onRetry} />
          ) : (
            <>
              <DropdownMenuItem
                onSelect={(event) => {
                  event.preventDefault();
                  toggleAll();
                }}
                className="gap-2"
              >
                <Checkbox
                  checked={allSelected ? true : value.length > 0 ? 'indeterminate' : false}
                  onCheckedChange={() => toggleAll()}
                  onClick={(event) => event.stopPropagation()}
                  size="sm"
                />
                <span>{allSelected ? 'Clear all' : 'Select all'}</span>
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              {options.map((opt) => (
                <DropdownMenuItem
                  key={opt.code}
                  onSelect={(event) => {
                    event.preventDefault();
                    setOne(opt.code, !selected.has(opt.code));
                  }}
                  className="gap-2"
                >
                  <Checkbox
                    checked={selected.has(opt.code)}
                    onCheckedChange={(checked) => setOne(opt.code, Boolean(checked))}
                    onClick={(event) => event.stopPropagation()}
                    size="sm"
                  />
                  <span>{opt.name || opt.code}</span>
                </DropdownMenuItem>
              ))}
            </>
          )}
        </DropdownMenuContent>
      </DropdownMenu>
      {value.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {value.map((code) => (
            <Badge key={code} variant="secondary" className="gap-1">
              {labelFor(code)}
              <button
                type="button"
                className="rounded-sm hover:text-destructive"
                onClick={() => setOne(code, false)}
                disabled={disabled}
              >
                <X className="size-3" />
              </button>
            </Badge>
          ))}
        </div>
      )}
    </div>
  );
}
