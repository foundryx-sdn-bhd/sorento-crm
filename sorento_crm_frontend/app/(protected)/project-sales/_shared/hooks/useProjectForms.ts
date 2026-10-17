import { useQuery } from '@tanstack/react-query';
import { fetchProjectForms, type ProjectFormKind } from '../lib/projectForms';

/** One form type's rows linked to a project, through that form's own list service. */
export function useProjectForms(kind: ProjectFormKind, projectId: string) {
  return useQuery({
    queryKey: ['project-forms', kind, projectId],
    queryFn: () => fetchProjectForms(kind, projectId),
    enabled: !!projectId,
    retry: 1,
  });
}
