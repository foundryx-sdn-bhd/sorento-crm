/**
 * The project link every staff form shares: the server sends `project_label`
 * (`CODE - Title`); one way to recognise the
 * backend's refusal of a bad project so the error can sit under the picker.
 */

export const PROJECT_LINK_NOT_FOUND = 'PROJECT_LINK_NOT_FOUND';

/** The staff 422 "That project was not found." (code on the body, message as the fallback). */
export function isProjectLinkError(error: unknown): boolean {
  if ((error as { code?: unknown } | null)?.code === PROJECT_LINK_NOT_FOUND) return true;
  return error instanceof Error && /project was not found/i.test(error.message);
}

/** What the free-text project name should pre-fill in the picker's search box: only while
 *  nothing is linked (AC-FP-14), and never a blank. */
export function projectSuggestionQuery(
  projectId: string | null | undefined,
  projectName: string | null | undefined,
): string | undefined {
  if (projectId) return undefined;
  const name = (projectName ?? '').trim();
  return name || undefined;
}
