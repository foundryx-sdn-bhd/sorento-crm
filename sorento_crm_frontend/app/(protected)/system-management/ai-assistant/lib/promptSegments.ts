/**
 * A prompt template as text and registry-variable segments (PLAN-prompt-dynamic-30sep R5a).
 * The editor draws each `var` segment as a chip; `joinSegments` is its exact inverse, so the
 * saved text is always what the owner sees, token for token.
 */
export type PromptSegment =
  | { kind: 'text'; text: string }
  | { kind: 'var'; name: string; raw: string };

const TOKEN_RE = /\{\{\s*([a-zA-Z0-9_]+)\s*\}\}/g;

export function splitTemplate(template: string, registryNames: string[]): PromptSegment[] {
  const names = new Set(registryNames);
  const out: PromptSegment[] = [];
  let cursor = 0;
  for (const m of template.matchAll(TOKEN_RE)) {
    if (!names.has(m[1])) continue;
    const start = m.index ?? 0;
    if (start > cursor) out.push({ kind: 'text', text: template.slice(cursor, start) });
    out.push({ kind: 'var', name: m[1], raw: m[0] });
    cursor = start + m[0].length;
  }
  if (cursor < template.length || out.length === 0) {
    out.push({ kind: 'text', text: template.slice(cursor) });
  }
  return out;
}

export function joinSegments(segments: PromptSegment[]): string {
  return segments.map((s) => (s.kind === 'text' ? s.text : s.raw)).join('');
}
