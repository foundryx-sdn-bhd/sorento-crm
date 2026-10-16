/**
 * Product code + name display rule (owner, 14 Oct 2026): wherever a product's code and name are
 * shown together, the name shows only when it differs from the code. Comparison ignores case,
 * outer whitespace and inner whitespace runs; a blank name counts as equal.
 */

const norm = (s: string | null | undefined): string =>
  (s ?? '').trim().replace(/\s+/g, ' ').toLowerCase();

/** True when both texts are equal under the display rule. */
export function sameDisplayText(a: string | null | undefined, b: string | null | undefined): boolean {
  return norm(a) === norm(b);
}

/** The name when it is non-blank and differs from the code, else null. */
export function distinctName(
  code: string | null | undefined,
  name: string | null | undefined
): string | null {
  if (!norm(name)) return null;
  return sameDisplayText(code, name) ? null : (name as string);
}

/** `code + sep + name`, or the code alone when the name equals the code or is blank. */
export function codeWithName(
  code: string | null | undefined,
  name: string | null | undefined,
  sep = ' - '
): string {
  const n = distinctName(code, name);
  return n === null ? (code ?? '') : `${code ?? ''}${sep}${n}`;
}
