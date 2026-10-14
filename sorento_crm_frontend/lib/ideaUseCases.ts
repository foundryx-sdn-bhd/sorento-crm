/** A use-cases value as a list: ss #123 sends one string (a line each), #124 a list. */
export function normalizeUseCases(value: unknown): string[] {
  const parts = Array.isArray(value) ? value : typeof value === 'string' ? value.split(/\r?\n/) : [];
  return parts.map((part) => String(part ?? '').trim()).filter(Boolean);
}
