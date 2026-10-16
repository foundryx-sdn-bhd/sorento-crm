/** How many values a tag's settings object sets (its leaves), for the tag list's Settings column. */
export function settingsCount(
  settings: Record<string, unknown> | null | undefined,
): number {
  let n = 0;
  for (const [section, value] of Object.entries(settings ?? {})) {
    if (value === null || value === undefined) continue;
    if (Array.isArray(value)) {
      n += section === 'agent_fields' ? value.length : 1;
    } else if (typeof value === 'object') {
      if (section === 'media') {
        for (const body of Object.values(value as Record<string, unknown>)) {
          n += body && typeof body === 'object' ? Object.keys(body).length : 0;
        }
      } else {
        n += Object.values(value as Record<string, unknown>).filter(
          (v) => v !== null && v !== undefined,
        ).length;
      }
    } else {
      n += 1;
    }
  }
  return n;
}
