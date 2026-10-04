'use client';

/**
 * The CRM's one phone number field (owner ruling, 29 Sep 2026: "a proper phone
 * number input ... default to malaysia so all phone number is cleansed").
 *
 * A country flag + dial code picker on the left (the standard
 * `SearchableSelect`, like every select in the system), the number on the
 * right as the national significant number (no trunk "0", since the dial code
 * is shown beside it), formatted for the chosen country as it is typed. The value in
 * and out is E.164 (`+60160000509`); `normalisePhone` is what turns "0160000509",
 * "60160000509" or "+60 16-000 0509" into that same value. The backend still
 * normalises what it receives - this is the first line, not the only one.
 *
 * An incomplete number shows the error state once the field is left (or when
 * the caller sets `showError`, e.g. on submit), never while it is being typed.
 */

import * as React from 'react';
import {
  AsYouType,
  getCountries,
  getCountryCallingCode,
  Metadata,
  parsePhoneNumberFromString,
  type CountryCode,
} from 'libphonenumber-js';
import { ChevronDown } from 'lucide-react';
import { cn } from '@/lib/utils';
import { inputVariants } from '@/components/ui/input';
import {
  SearchableSelect,
  type SearchableSelectOption,
} from '@/components/common/SearchableSelect';

export const DEFAULT_PHONE_COUNTRY: CountryCode = 'MY';

export type NormalisedPhone = {
  /** E.164, or '' when nothing was typed. Present (best effort) even while incomplete. */
  e164: string;
  /** What the field shows: national format for the resolved country. */
  display: string;
  /** The country the number resolved to (a pasted "+65 ..." switches it). */
  country: CountryCode;
  /** A complete, valid number for `country`. */
  valid: boolean;
};

/**
 * Normalise whatever was typed or pasted against `country`. A leading "+" or
 * "00" means international; so does a run of digits that starts with the
 * country's own calling code and only makes a valid number read that way
 * ("60160000509" in Malaysia).
 */
export function normalisePhone(raw: string, country: CountryCode): NormalisedPhone {
  const trimmed = raw.trim();
  let digits = trimmed.replace(/\D/g, '');
  if (!digits) return { e164: '', display: trimmed.startsWith('+') ? '+' : '', country, valid: false };

  let international = trimmed.startsWith('+');
  if (!international && trimmed.startsWith('00')) {
    international = true;
    digits = digits.slice(2);
  }
  if (!international && digits.startsWith(getCountryCallingCode(country))) {
    const asNational = parsePhoneNumberFromString(digits, country);
    const asInternational = parsePhoneNumberFromString(`+${digits}`);
    if (asInternational?.isValid() && !asNational?.isValid()) international = true;
  }

  const typer = international ? new AsYouType() : new AsYouType(country);
  const typed = typer.input(international ? `+${digits}` : digits);
  const number = typer.getNumber();
  const resolved = (number?.country ?? typer.getCountry() ?? country) as CountryCode;
  const valid = Boolean(number?.isValid());
  const shownCountry = valid ? resolved : country;
  const shownCode = getCountryCallingCode(shownCountry);
  const callingCode = number?.countryCallingCode ?? typer.getCallingCode() ?? (international ? undefined : shownCode);

  // The dial code sits beside the field, so the field shows the national
  // significant number without the trunk "0" (owner ruling, 29 Sep 2026:
  // "16-000 0509", not "016-000 0509"). A half-typed "+65 9" in a Malaysia
  // field keeps its "+" until it resolves, or the "+60" beside it would lie.
  let display = typed;
  if (callingCode === shownCode) {
    const nsn = valid && number ? number.nationalNumber : (typer.getNationalNumber() ?? '');
    // The national format is the per-country one ("016-000 0509"); it only
    // formats right with the trunk prefix present, so format first, then drop it.
    const formatted =
      valid && number
        ? number.formatNational()
        : new AsYouType(shownCountry).input(`${nationalPrefix(shownCountry)}${nsn}`);
    display = withoutTrunkPrefix(formatted, nsn);
  }

  return {
    e164: number?.number ?? (international ? `+${digits}` : ''),
    display,
    country: shownCountry,
    valid,
  };
}

function nationalPrefix(country: CountryCode): string {
  const metadata = new Metadata();
  metadata.selectNumberingPlan(country);
  // Present at runtime ("0" for MY, "8" for RU), missing from the published typings.
  const plan = metadata.numberingPlan as unknown as { nationalPrefix?: () => string | undefined } | undefined;
  return plan?.nationalPrefix?.() ?? '';
}

/** "016-000 0509" to "16-000 0509": drop the digits in front of the national significant number. */
function withoutTrunkPrefix(formatted: string, nsn: string): string {
  const extra = formatted.replace(/\D/g, '').length - nsn.length;
  if (!nsn) return '';
  if (extra <= 0) return formatted;
  return formatted.replace(new RegExp(`^\\D*(?:\\d\\D*?){${extra}}[\\s-]*`), '');
}

/** Regional-indicator flag emoji for an ISO 3166 alpha-2 code. */
export function flagEmoji(country: string): string {
  return String.fromCodePoint(...[...country.toUpperCase()].map((c) => 0x1f1e6 + c.charCodeAt(0) - 65));
}

let countryOptionsCache: SearchableSelectOption[] | null = null;

function countryOptions(): SearchableSelectOption[] {
  if (countryOptionsCache) return countryOptionsCache;
  const names = new Intl.DisplayNames(['en'], { type: 'region' });
  countryOptionsCache = getCountries()
    .map((code) => {
      const name = names.of(code) ?? code;
      const dial = `+${getCountryCallingCode(code)}`;
      return { value: code, label: name, description: dial, searchText: `${name} ${code} ${dial}` };
    })
    .sort((a, b) => a.label.localeCompare(b.label));
  return countryOptionsCache;
}

export type PhoneInputProps = {
  /** E.164 value, or '' for empty. */
  value: string;
  /** Called on every edit with the E.164 value and whether it is a complete number. */
  onChange: (e164: string, meta: { valid: boolean; country: CountryCode }) => void;
  defaultCountry?: CountryCode;
  id?: string;
  name?: string;
  disabled?: boolean;
  autoFocus?: boolean;
  /** Force the error state (e.g. after a submit with an incomplete number). */
  showError?: boolean;
  errorMessage?: string;
  className?: string;
  'aria-label'?: string;
  onBlur?: () => void;
  onKeyDown?: React.KeyboardEventHandler<HTMLInputElement>;
};

export function PhoneInput({
  value,
  onChange,
  defaultCountry = DEFAULT_PHONE_COUNTRY,
  id,
  name,
  disabled = false,
  autoFocus,
  showError = false,
  errorMessage = 'Enter a complete phone number.',
  className,
  'aria-label': ariaLabel,
  onBlur,
  onKeyDown,
}: PhoneInputProps) {
  const initial = React.useMemo(
    () => (value ? normalisePhone(value, defaultCountry) : null),
    // Only the first render seeds the field; later `value` changes are synced below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );
  const [country, setCountry] = React.useState<CountryCode>(initial?.country ?? defaultCountry);
  const [display, setDisplay] = React.useState(initial?.display ?? '');
  const [valid, setValid] = React.useState(initial?.valid ?? false);
  const [touched, setTouched] = React.useState(false);
  const lastEmitted = React.useRef(value);

  // A caller that resets or replaces the value (not an echo of our own edit).
  React.useEffect(() => {
    if (value === lastEmitted.current) return;
    lastEmitted.current = value;
    const next = normalisePhone(value, country);
    setDisplay(next.display);
    setValid(next.valid);
    if (next.valid) setCountry(next.country);
    if (!value) setTouched(false);
  }, [value, country]);

  const apply = (raw: string, forCountry: CountryCode) => {
    const next = normalisePhone(raw, forCountry);
    setDisplay(next.display);
    setValid(next.valid);
    setCountry(next.country);
    lastEmitted.current = next.e164;
    onChange(next.e164, { valid: next.valid, country: next.country });
  };

  const errorId = id ? `${id}-error` : undefined;
  const invalid = Boolean(display.replace(/\D/g, '')) && !valid && (touched || showError);
  const dial = `+${getCountryCallingCode(country)}`;

  return (
    <div className={cn('space-y-1.5', className)}>
      <div
        data-slot="phone-input"
        data-invalid={invalid || undefined}
        data-disabled={disabled || undefined}
        className={cn(
          inputVariants(),
          'items-stretch p-0 overflow-hidden',
          'has-[input:focus-visible]:border-ring has-[input:focus-visible]:ring-[3px] has-[input:focus-visible]:ring-ring/30',
          'data-invalid:border-destructive/60 data-invalid:ring-[3px] data-invalid:ring-destructive/10',
          'dark:data-invalid:border-destructive dark:data-invalid:ring-destructive/20',
          'data-disabled:cursor-not-allowed data-disabled:opacity-60',
        )}
      >
        <SearchableSelect
          value={country}
          onChange={(code) => {
            if (!code) return;
            // Carry the subscriber digits across, not the old country's trunk "0".
            const current = normalisePhone(display, country);
            const carried = current.valid
              ? (parsePhoneNumberFromString(current.e164)?.nationalNumber ?? display)
              : display;
            apply(carried, code as CountryCode);
          }}
          options={countryOptions()}
          disabled={disabled}
          wrapOptions
          renderOption={(opt) => (
            <span className="flex items-center gap-2">
              <span aria-hidden className="text-base leading-none">
                {flagEmoji(opt.value)}
              </span>
              <span className="flex-1">{opt.label}</span>
              <span className="text-muted-foreground tabular-nums">{opt.description}</span>
            </span>
          )}
          renderTrigger={({ open }) => (
            <button
              type="button"
              disabled={disabled}
              aria-label={`Country: ${countryOptions().find((o) => o.value === country)?.label ?? country} (${dial})`}
              aria-expanded={open}
              data-slot="phone-input-country"
              className="flex shrink-0 items-center gap-1 border-e border-input bg-muted/40 ps-2.5 pe-2 outline-none hover:bg-muted focus-visible:bg-muted disabled:cursor-not-allowed"
            >
              <span aria-hidden className="text-base leading-none">
                {flagEmoji(country)}
              </span>
              <ChevronDown aria-hidden className="size-3.5 opacity-60" />
            </button>
          )}
        />
        <span
          aria-hidden
          className="flex shrink-0 items-center ps-2.5 text-muted-foreground tabular-nums select-none"
        >
          {dial}
        </span>
        <input
          id={id}
          name={name}
          data-slot="phone-input-number"
          type="tel"
          inputMode="tel"
          autoComplete="tel"
          autoFocus={autoFocus}
          disabled={disabled}
          aria-label={ariaLabel}
          aria-invalid={invalid || undefined}
          aria-describedby={invalid ? errorId : undefined}
          value={display}
          placeholder={country === 'MY' ? '12-345 6789' : undefined}
          onChange={(e) => apply(e.target.value, country)}
          onBlur={() => {
            setTouched(true);
            onBlur?.();
          }}
          onKeyDown={onKeyDown}
          className="min-w-0 flex-1 bg-transparent px-2 text-foreground tabular-nums outline-none placeholder:text-muted-foreground/80 disabled:cursor-not-allowed"
        />
      </div>
      {invalid ? (
        <p id={errorId} role="alert" className="text-xs text-destructive">
          {errorMessage}
        </p>
      ) : null}
    </div>
  );
}
