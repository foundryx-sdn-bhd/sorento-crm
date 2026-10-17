'use client';

/**
 * The line table of the new product application form, shared by the staff page and the portal
 * so the two cannot drift. The selects are fetchers handed in as props (the portal passes its
 * own lookups); `hideSupplierAndCost` drops the supplier, supplier model no and cost columns for
 * the portal. Every select is clearable and every number box can be emptied (sent as null).
 * The table scrolls sideways inside its own box; the page never does.
 */

import { Trash2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { NumberInput } from '@/components/ui/number-input';
import {
  SearchableSelect,
  type SearchableSelectOption,
} from '@/components/common/SearchableSelect';
import { numberFieldError } from '@/lib/number-field';
import type { DraftLine } from '@/lib/new-product-application-form';

export type LookupFetcher = (
  query: string,
  pageIndex: number,
) => Promise<SearchableSelectOption[]>;

export interface NewProductApplicationLookups {
  itemGroups: LookupFetcher;
  uoms: LookupFetcher;
  brands: LookupFetcher;
  itemTypes: LookupFetcher;
  /** Not read when `hideSupplierAndCost` is set. */
  suppliers?: LookupFetcher;
}

export interface NewProductApplicationLinesProps {
  lines: DraftLine[];
  onChange: (lines: DraftLine[]) => void;
  lookups: NewProductApplicationLookups;
  /** The portal passes true: no supplier, supplier model no or cost columns. */
  hideSupplierAndCost?: boolean;
  /** Keys are `<field>_<line key>` (number problems) plus `lines`. */
  errors?: Record<string, string>;
  disabled?: boolean;
}

type PickField = 'category' | 'base_uom' | 'brand' | 'item_type' | 'supplier';

const SUPPLIER_PAGE_SIZE = 50;

export function NewProductApplicationLines({
  lines,
  onChange,
  lookups,
  hideSupplierAndCost,
  errors = {},
  disabled,
}: NewProductApplicationLinesProps) {
  const patchLine = (key: string, patch: Partial<DraftLine>) =>
    onChange(lines.map((l) => (l.key === key ? { ...l, ...patch } : l)));

  const pick = (
    line: DraftLine,
    field: PickField,
    label: string,
    fetchOptions: LookupFetcher | undefined,
    paginated = false,
  ) => {
    const id = line[`${field}_id` as const];
    const shown = line[`${field}_label` as keyof DraftLine] as string;
    return (
      <SearchableSelect
        aria-label={label}
        value={id}
        disabled={disabled}
        clearable
        truncateTriggerLabel
        wrapOptions
        placeholder={`Search ${label.toLowerCase()}`}
        fetchOptions={fetchOptions}
        paginated={paginated}
        pageSize={paginated ? SUPPLIER_PAGE_SIZE : undefined}
        selectedOption={id ? { value: id, label: shown } : undefined}
        onChange={(v) => {
          if (!v)
            patchLine(line.key, {
              [`${field}_id`]: '',
              [`${field}_label`]: '',
            });
        }}
        onOptionChange={(opt) =>
          opt &&
          patchLine(line.key, {
            [`${field}_id`]: opt.value,
            [`${field}_label`]: opt.label,
          })
        }
      />
    );
  };

  const figure = (
    line: DraftLine,
    field: 'cost_price' | 'selling_price' | 'expected_qty',
    label: string,
    rules: Parameters<typeof numberFieldError>[1],
  ) => (
    <NumberInput
      inputMode="decimal"
      step="any"
      aria-label={label}
      disabled={disabled}
      value={line[field]}
      error={
        errors[`${field}_${line.key}`]
          ? numberFieldError(line[field], rules)
          : undefined
      }
      onChange={(e) => patchLine(line.key, { [field]: e.target.value })}
    />
  );

  const text = (line: DraftLine, field: keyof DraftLine, label: string) => (
    <Input
      aria-label={label}
      disabled={disabled}
      value={line[field]}
      onChange={(e) => patchLine(line.key, { [field]: e.target.value })}
    />
  );

  const th = 'px-2 py-2 text-left font-medium';
  return (
    <div>
      <div className="relative overflow-x-auto">
        <table
          className={`w-full table-fixed text-sm ${hideSupplierAndCost ? 'min-w-[1100px]' : 'min-w-[1600px]'}`}
        >
          <thead className="bg-muted/40 text-xs uppercase tracking-wide text-muted-foreground">
            <tr>
              <th className={`${th} w-12`}>No.</th>
              <th className={`${th} w-40`}>Proposed code</th>
              <th className={`${th} w-56`}>Description *</th>
              <th className={`${th} w-44`}>Item group *</th>
              <th className={`${th} w-28`}>Base UOM *</th>
              <th className={`${th} w-36`}>Brand</th>
              <th className={`${th} w-36`}>Item type</th>
              {!hideSupplierAndCost && (
                <th className={`${th} w-44`}>Supplier</th>
              )}
              {!hideSupplierAndCost && (
                <th className={`${th} w-36`}>Supplier model no</th>
              )}
              {!hideSupplierAndCost && (
                <th className={`${th} w-28`}>Cost (MYR)</th>
              )}
              <th className={`${th} w-28`}>Selling (MYR)</th>
              <th className={`${th} w-24`}>Exp. qty</th>
              <th className={`${th} w-48`}>Further description</th>
              <th className={`${th} w-44`}>Remark</th>
              <th className="w-10 px-2 py-2">
                <span className="sr-only">Remove</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {lines.map((line, index) => (
              <tr
                key={line.key}
                data-testid="npa-line"
                className="border-t align-top"
              >
                <td className="px-2 py-2 text-muted-foreground">{index + 1}</td>
                <td className="px-2 py-2">
                  {text(line, 'proposed_code', 'Proposed code')}
                </td>
                <td className="px-2 py-2">
                  {text(line, 'description', 'Description')}
                </td>
                <td className="px-2 py-2">
                  {pick(line, 'category', 'Item group', lookups.itemGroups)}
                </td>
                <td className="px-2 py-2">
                  {pick(line, 'base_uom', 'Base UOM', lookups.uoms)}
                </td>
                <td className="px-2 py-2">
                  {pick(line, 'brand', 'Brand', lookups.brands)}
                </td>
                <td className="px-2 py-2">
                  {pick(line, 'item_type', 'Item type', lookups.itemTypes)}
                </td>
                {!hideSupplierAndCost && (
                  <td className="px-2 py-2">
                    {pick(
                      line,
                      'supplier',
                      'Supplier',
                      lookups.suppliers,
                      true,
                    )}
                  </td>
                )}
                {!hideSupplierAndCost && (
                  <td className="px-2 py-2">
                    {text(line, 'supplier_item_code', 'Supplier model no')}
                  </td>
                )}
                {!hideSupplierAndCost && (
                  <td className="px-2 py-2">
                    {figure(line, 'cost_price', 'Cost price', {
                      min: 0,
                      optional: true,
                    })}
                  </td>
                )}
                <td className="px-2 py-2">
                  {figure(line, 'selling_price', 'Selling price', {
                    min: 0,
                    optional: true,
                  })}
                </td>
                <td className="px-2 py-2">
                  {figure(line, 'expected_qty', 'Expected quantity', {
                    min: 0,
                    minExclusive: true,
                    optional: true,
                  })}
                </td>
                <td className="px-2 py-2">
                  {text(line, 'further_description', 'Further description')}
                </td>
                <td className="px-2 py-2">{text(line, 'remark', 'Remark')}</td>
                <td className="px-2 py-2">
                  {!disabled && (
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      aria-label="Remove line"
                      onClick={() =>
                        onChange(lines.filter((l) => l.key !== line.key))
                      }
                    >
                      <Trash2 className="size-4" />
                    </Button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {errors.lines && (
        <p className="mt-2 text-xs text-destructive">{errors.lines}</p>
      )}
    </div>
  );
}
