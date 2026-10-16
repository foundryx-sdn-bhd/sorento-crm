import type { RespondContact } from '../types/contact.types';

/** The Tag settings column as text (also what the toolbar export writes): Matches, "Differs: n" or "-". */
export function tagSettingsLabel(
  ts: RespondContact['tag_settings'] | undefined,
): string {
  if (!ts || ts.status === 'none') return '-';
  return ts.status === 'matches' ? 'Matches' : `Differs: ${ts.differ_count}`;
}
