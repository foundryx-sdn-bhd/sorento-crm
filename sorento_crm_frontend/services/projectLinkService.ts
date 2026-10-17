import { apiFetch } from '@/lib/api';
import { buildDataGridParams, extractApiError } from '@/lib/api-client';

/**
 * Projects for a form's "Project (optional)" picker.
 *
 * Server-searched rather than pre-loaded: there will be hundreds of pursuits, and a static
 * list silently caps at whatever the first page held. The label is the project CODE plus the
 * title, because the code is what people quote to each other and the title is what they
 * recognise -- and the UUID never reaches the screen.
 */
export async function searchProjectsForLink(
  query: string,
  page = 1,
): Promise<{ value: string; label: string; description?: string }[]> {
  const params = buildDataGridParams({
    pageIndex: page - 1,
    pageSize: 25,
    sorting: [{ id: 'updated_at', desc: true }],
    searchQuery: query.trim(),
  });
  const response = await apiFetch(`/api/v1/project-sales/projects/?${params.toString()}`);
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to load projects'));
  }
  const body = await response.json();
  return (body.data ?? []).map(
    (row: {
      id: string;
      project_code: string;
      title: string;
      developer_name?: string | null;
      status_label?: string | null;
    }) => ({
      value: row.id,
      label: `${row.project_code} - ${row.title}`,
      description: [row.developer_name, row.status_label].filter(Boolean).join(' - ') || undefined,
    }),
  );
}
