'use client';

import { useQuery } from '@tanstack/react-query';
import {
  getProjectSalesStatusGraph,
  type ProjectSalesGraphEntity,
} from '../services/projectStatusGraphService';

export const projectStatusGraphKey = (entityType: ProjectSalesGraphEntity, scopeId?: string | null) => [
  'project-sales-status-graph',
  entityType,
  scopeId ?? null,
];

/** A project, task or lead status graph, readable by every project viewer. */
export function useProjectStatusGraph(entityType: ProjectSalesGraphEntity, scopeId?: string | null) {
  return useQuery({
    queryKey: projectStatusGraphKey(entityType, scopeId),
    queryFn: () => getProjectSalesStatusGraph(entityType, scopeId),
  });
}
