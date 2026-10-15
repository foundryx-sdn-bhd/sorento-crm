'use client';

import * as React from 'react';

import { useMutation, useQueries, useQuery, useQueryClient } from '@tanstack/react-query';
import type { QueryKey } from '@tanstack/react-query';
import type { ListPagerPage, ListPagerParams } from '@/hooks/useListPager';
import { toast } from '@/lib/toast';
import { getProductSetsForLineSelect } from '@/app/(protected)/master-data-management/product-sets/services/productSetService';
import {
  addStakeholder,
  changeProjectStatus,
  changeTaskStatus,
  createProjectTask,
  createTemplateTask,
  deleteProjectTask,
  deleteTemplateTask,
  getTaskHistory,
  listMyTasks,
  listProjectTasks,
  listTemplateTasks,
  updateProjectTask,
  updateTemplateTask,
  changeLeadStatus,
  createQuotation,
  createQuotationLine,
  createSeries,
  deletePriceFloor,
  deleteQuotation,
  deleteQuotationLine,
  deleteSeries,
  getEffectivePriceFloor,
  getSeriesPricedRows,
  getSeriesProductRows,
  getSeriesSetRows,
  removeSeriesSet,
  updateSeriesSetPricing,
  getImportJobStatus,
  judgeQuotationLine,
  importSeriesProducts,
  removeSeriesProduct,
  updateSeriesProductPricing,
  listPriceFloors,
  listQuotationLines,
  listQuotationLossReasons,
  listQuotationVersions,
  listQuotations,
  listSeries,
  recomputeQuotationVersion,
  reviseQuotation,
  setQuotationOutcome,
  updateQuotation,
  replaceQuotationLines,
  updateQuotationLine,
  updateSeries,
  uploadSeriesProducts,
  upsertPriceFloor,
  listSamples,
  createSample,
  updateSample,
  deleteSample,
  listPurchaseOrders,
  createPurchaseOrder,
  updatePurchaseOrder,
  deletePurchaseOrder,
  listPurchaseOrderLines,
  createPurchaseOrderLine,
  updatePurchaseOrderLine,
  deletePurchaseOrderLine,
  listProjectSponsorships,
  getSponsorshipRollup,
  getProjectDashboard,
  createLead,
  createParty,
  deleteLead,
  disqualifyLead,
  getCustomerPortfolio,
  getLead,
  getLeadMetrics,
  listDisqualifyReasons,
  listLeads,
  previewQualify,
  qualifyLead,
  reopenLead,
  updateLead,
  createProjectTemplate,
  createProjectType,
  deleteProjectTemplate,
  deleteProjectType,
  updateProjectTemplate,
  updateProjectType,
  createTakeoverRequest,
  decideTakeoverRequest,
  deleteParty,
  deleteProject,
  getProject,
  listCollaborators,
  listParties,
  listProjectTemplates,
  getQuotationLineImages,
  listProjectTypes,
  listProjects,
  listStakeholders,
  listTakeoverRequests,
  previewClashes,
  registerProject,
  removeStakeholder,
  updateParty,
  updateProject,
  updateStakeholder,
} from '../services/projectService';
import type {
  ProjectListParams,
  ProjectPartyBody,
  ProjectRegisterBody,
  ProjectStakeholderBody,
  ProjectTaskBody,
  LeadListParams,
  FloorTargetLevel,
  PriceFloorRuleBody,
  ProjectSampleBody,
  ProjectPurchaseOrderBody,
  ProjectPurchaseOrderSaveBody,
  PurchaseOrderLineBody,
  ProjectQuotation,
  ProjectQuotationBody,
  ProjectSeriesBody,
  QuotationLineBody,
  QuotationLineBulkItem,
  QuotationOutcomeBody,
  SeriesProductImportBody,
  LeadQualifyBody,
  ProjectLeadBody,
  ProjectTemplateBody,
  ProjectTypeBody,
  ProjectTemplateTaskBody,
  ProjectUpdateBody,
  TaskPhase,
  TaskStatusChangeBody,
} from '../types/project.types';
import { importJobPhase } from '../types/project.types';
// Keys only, and the document hooks import nothing from here, so the two files do not circle.
import { quotationDocumentsKey } from './useQuotationDocuments';
import { LIST_QUERY_OPTIONS } from '@/lib/list-query/options';

export const PROJECTS_KEY = 'projects';
export const PARTIES_KEY = 'project-parties';
export const PROJECT_TYPES_KEY = ['project-types'];

export const projectsListKey = (params: ProjectListParams) => [PROJECTS_KEY, 'list', params];
export const projectKey = (projectId: string) => [PROJECTS_KEY, 'detail', projectId];
export const stakeholdersKey = (projectId: string) => [PROJECTS_KEY, 'stakeholders', projectId];
export const collaboratorsKey = (projectId: string) => [PROJECTS_KEY, 'collaborators', projectId];
export const takeoverKey = (projectId: string) => [PROJECTS_KEY, 'takeover', projectId];

/**
 * The pipeline list a detail URL describes, in the shape `PipelineClient` passes.
 *
 * Same object, same key: the record's pager then walks the page the reader was on,
 * out of the cache the list already filled, without a request of its own.
 */
export function projectsListParamsFromUrl(params: ListPagerParams): ProjectListParams {
  return {
    query: params.searchQuery || undefined,
    developer_party_id: params.filters.developer_party_id
      ? [params.filters.developer_party_id]
      : undefined,
    owner_user_id: params.filters.owner_user_id
      ? [params.filters.owner_user_id]
      : undefined,
    type_id: params.filters.type_id ? [params.filters.type_id] : undefined,
    only_critical: params.filters.only_critical === 'true' || undefined,
    page: params.pageIndex + 1,
    limit: params.pageSize,
    sort: params.sorting[0]?.id ?? 'created_at',
    dir: (params.sorting[0]?.desc ?? true ? 'desc' : 'asc') as 'asc' | 'desc',
  };
}

/** The pager's two hooks into the pipeline list. */
export const projectsPagerQuery = {
  listQueryKey: (params: ListPagerParams): QueryKey =>
    projectsListKey(projectsListParamsFromUrl(params)),
  fetchPage: (params: ListPagerParams): Promise<ListPagerPage> =>
    listProjects(projectsListParamsFromUrl(params)),
};

export function useProjects(params: ProjectListParams) {
  return useQuery({
    ...LIST_QUERY_OPTIONS,
    queryKey: projectsListKey(params),
    queryFn: () => listProjects(params),
  });
}

export function useProject(projectId: string | undefined) {
  return useQuery({
    queryKey: projectKey(projectId ?? ''),
    queryFn: () => getProject(projectId as string),
    enabled: Boolean(projectId),
  });
}

export function useProjectTypes() {
  return useQuery({ queryKey: PROJECT_TYPES_KEY, queryFn: () => listProjectTypes() });
}

export function useProjectTemplates(typeId?: string) {
  return useQuery({
    queryKey: ['project-templates', typeId ?? null],
    queryFn: () => listProjectTemplates(typeId),
  });
}

export function useProjectParties(params: {
  party_type?: string;
  query?: string;
  page?: number;
  limit?: number;
  include_inactive?: boolean;
}) {
  return useQuery({
    ...LIST_QUERY_OPTIONS,
    queryKey: [PARTIES_KEY, params],
    queryFn: () => listParties(params),
  });
}

export function useStakeholders(projectId: string | undefined) {
  return useQuery({
    queryKey: stakeholdersKey(projectId ?? ''),
    queryFn: () => listStakeholders(projectId as string),
    enabled: Boolean(projectId),
  });
}

export function useCollaborators(projectId: string | undefined) {
  return useQuery({
    queryKey: collaboratorsKey(projectId ?? ''),
    queryFn: () => listCollaborators(projectId as string),
    enabled: Boolean(projectId),
  });
}

export function useTakeoverRequests(projectId: string | undefined) {
  return useQuery({
    queryKey: takeoverKey(projectId ?? ''),
    queryFn: () => listTakeoverRequests(projectId as string),
    enabled: Boolean(projectId),
  });
}

/**
 * Duplicate check for a project title, run on demand (the Register dialog's Check
 * button) rather than on every keystroke: the owner found the live check tiring
 * (hand test, PR #1336).
 *
 * `title` is the value the user last asked about, not the live field. Below a
 * minimum length nothing is asked, because two characters match half the pipeline.
 */
export const CLASH_MIN_CHARS = 4;

function clashKey(title: string, developerPartyId?: string | null): QueryKey {
  return ['project-clash', title.trim().toLowerCase(), developerPartyId ?? null];
}

export function useClashPreview(title: string, developerPartyId?: string | null) {
  const trimmed = title.trim();
  return useQuery({
    queryKey: clashKey(trimmed, developerPartyId),
    queryFn: () => previewClashes({ title: trimmed, developer_party_id: developerPartyId }),
    enabled: trimmed.length >= CLASH_MIN_CHARS,
    staleTime: 30_000,
  });
}

/**
 * The same check as `useClashPreview`, awaited: the one guard Register runs on
 * submit. It fills the same cache entry, so the inline panel shows its answer.
 */
export function useFetchClashPreview() {
  const queryClient = useQueryClient();
  return React.useCallback(
    (title: string, developerPartyId?: string | null) => {
      const trimmed = title.trim();
      return queryClient.fetchQuery({
        queryKey: clashKey(trimmed, developerPartyId),
        queryFn: () => previewClashes({ title: trimmed, developer_party_id: developerPartyId }),
      });
    },
    [queryClient],
  );
}

function useInvalidateProjects() {
  const queryClient = useQueryClient();
  return (projectId?: string) => {
    queryClient.invalidateQueries({ queryKey: [PROJECTS_KEY] });
    if (projectId) queryClient.invalidateQueries({ queryKey: projectKey(projectId) });
  };
}

export function useRegisterProject() {
  const invalidate = useInvalidateProjects();
  return useMutation({
    mutationFn: (body: ProjectRegisterBody) => registerProject(body),
    onSuccess: (project) => {
      invalidate();
      toast.success(`${project.project_code} registered`);
    },
    onError: (error: Error) => toast.error(error.message),
  });
}

export function useUpdateProject(projectId: string) {
  const invalidate = useInvalidateProjects();
  return useMutation({
    mutationFn: (body: ProjectUpdateBody) => updateProject(projectId, body),
    onSuccess: () => {
      invalidate(projectId);
      toast.success('Project saved');
    },
    onError: (error: Error) => toast.error(error.message),
  });
}

/**
 * A rejected move must put the card back where it was, so the caller re-reads the
 * list rather than trusting its optimistic position (AC-G3).
 */
export function useChangeProjectStatus() {
  const invalidate = useInvalidateProjects();
  return useMutation({
    mutationFn: ({ projectId, toStatusId }: { projectId: string; toStatusId: string }) =>
      changeProjectStatus(projectId, toStatusId),
    onSuccess: (project) => {
      invalidate(project.id);
      toast.success(`${project.project_code} moved to ${project.status_label ?? 'a new stage'}`);
    },
    onError: (error: Error, variables) => {
      invalidate(variables.projectId);
      toast.error(error.message);
    },
  });
}

export function useDeleteProject() {
  const invalidate = useInvalidateProjects();
  return useMutation({
    mutationFn: (projectId: string) => deleteProject(projectId),
    onSuccess: () => {
      invalidate();
      toast.success('Project deleted');
    },
    onError: (error: Error) => toast.error(error.message),
  });
}

export function useStakeholderMutations(projectId: string) {
  const queryClient = useQueryClient();
  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: stakeholdersKey(projectId) });

  const add = useMutation({
    mutationFn: (body: ProjectStakeholderBody) => addStakeholder(projectId, body),
    onSuccess: () => {
      invalidate();
      toast.success('Stakeholder added');
    },
    onError: (error: Error) => toast.error(error.message),
  });

  const update = useMutation({
    mutationFn: ({ id, body }: { id: string; body: Partial<ProjectStakeholderBody> }) =>
      updateStakeholder(projectId, id, body),
    onSuccess: () => {
      invalidate();
      toast.success('Stakeholder saved');
    },
    onError: (error: Error) => toast.error(error.message),
  });

  const remove = useMutation({
    mutationFn: (id: string) => removeStakeholder(projectId, id),
    onSuccess: () => {
      invalidate();
      toast.success('Stakeholder removed');
    },
    onError: (error: Error) => toast.error(error.message),
  });

  return { add, update, remove };
}

export function usePartyMutations() {
  const queryClient = useQueryClient();
  const invalidate = () => queryClient.invalidateQueries({ queryKey: [PARTIES_KEY] });

  const create = useMutation({
    mutationFn: (body: ProjectPartyBody) => createParty(body),
    onSuccess: (party) => {
      invalidate();
      toast.success(`${party.name} added`);
    },
    onError: (error: Error) => toast.error(error.message),
  });

  const update = useMutation({
    mutationFn: ({ id, body }: { id: string; body: Partial<ProjectPartyBody> }) =>
      updateParty(id, body),
    onSuccess: () => {
      invalidate();
      toast.success('Party saved');
    },
    onError: (error: Error) => toast.error(error.message),
  });

  const remove = useMutation({
    mutationFn: (id: string) => deleteParty(id),
    onSuccess: () => {
      invalidate();
      toast.success('Party deleted');
    },
    onError: (error: Error) => toast.error(error.message),
  });

  return { create, update, remove };
}

export function useTakeoverMutations(projectId: string) {
  const queryClient = useQueryClient();
  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: takeoverKey(projectId) });
    queryClient.invalidateQueries({ queryKey: collaboratorsKey(projectId) });
    queryClient.invalidateQueries({ queryKey: projectKey(projectId) });
  };

  const request = useMutation({
    mutationFn: (body: { kind: 'join' | 'dispute'; reason: string }) =>
      createTakeoverRequest(projectId, body),
    onSuccess: (created) => {
      invalidate();
      toast.success(
        created.kind === 'join'
          ? 'Request sent to the project owner'
          : 'Dispute raised with the sales manager',
      );
    },
    onError: (error: Error) => toast.error(error.message),
  });

  const decide = useMutation({
    mutationFn: ({
      id,
      approve,
      note,
    }: {
      id: string;
      approve: boolean;
      note?: string | null;
    }) => decideTakeoverRequest(projectId, id, { approve, decision_note: note }),
    onSuccess: (decided) => {
      invalidate();
      toast.success(decided.status === 'approved' ? 'Request approved' : 'Request rejected');
    },
    onError: (error: Error) => toast.error(error.message),
  });

  return { request, decide };
}

// ------------------------------------------------------------------- tasks

export const tasksKey = (projectId: string, phase?: TaskPhase) => [
  PROJECTS_KEY,
  'tasks',
  projectId,
  phase ?? 'all',
];
export const MY_TASKS_KEY = 'my-tasks';
export const taskHistoryKey = (projectId: string, taskId: string) => [
  PROJECTS_KEY,
  'task-history',
  projectId,
  taskId,
];
export const templateTasksKey = (templateId: string) => ['project-template-tasks', templateId];

export function useProjectTasks(projectId: string | undefined, phase?: TaskPhase) {
  return useQuery({
    queryKey: tasksKey(projectId ?? '', phase),
    queryFn: () => listProjectTasks(projectId as string, phase),
    enabled: Boolean(projectId),
  });
}

export function useMyTasks(params: {
  include_unassigned_owned?: boolean;
  page?: number;
  limit?: number;
}) {
  return useQuery({
    ...LIST_QUERY_OPTIONS,
    queryKey: [MY_TASKS_KEY, params],
    queryFn: () => listMyTasks(params),
  });
}

export function useTaskHistory(projectId: string, taskId: string | null) {
  return useQuery({
    queryKey: taskHistoryKey(projectId, taskId ?? ''),
    queryFn: () => getTaskHistory(projectId, taskId as string),
    enabled: Boolean(taskId),
  });
}

/**
 * Every task write invalidates the PROJECT too.
 *
 * The project's next action is derived from its earliest open task (AC-N6), so
 * completing a task changes the project row the pipeline renders. Without this the
 * board keeps showing a next action that has already been dealt with.
 */
export function useTaskMutations(projectId: string) {
  const queryClient = useQueryClient();
  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: [PROJECTS_KEY, 'tasks', projectId] });
    queryClient.invalidateQueries({ queryKey: projectKey(projectId) });
    queryClient.invalidateQueries({ queryKey: [PROJECTS_KEY, 'list'] });
    queryClient.invalidateQueries({ queryKey: [MY_TASKS_KEY] });
  };

  const create = useMutation({
    mutationFn: (body: ProjectTaskBody) => createProjectTask(projectId, body),
    onSuccess: () => {
      invalidate();
      toast.success('Task added');
    },
    onError: (error: Error) => toast.error(error.message),
  });

  const update = useMutation({
    mutationFn: ({ id, body }: { id: string; body: Partial<ProjectTaskBody> }) =>
      updateProjectTask(projectId, id, body),
    onSuccess: () => {
      invalidate();
      toast.success('Task saved');
    },
    onError: (error: Error) => toast.error(error.message),
  });

  const move = useMutation({
    mutationFn: ({ id, body }: { id: string; body: TaskStatusChangeBody }) =>
      changeTaskStatus(projectId, id, body),
    onSuccess: (task) => {
      invalidate();
      queryClient.invalidateQueries({ queryKey: taskHistoryKey(projectId, task.id) });
      toast.success(`"${task.name}" moved to ${task.status_label ?? 'a new status'}`);
    },
    onError: (error: Error) => toast.error(error.message),
  });

  const remove = useMutation({
    mutationFn: (id: string) => deleteProjectTask(projectId, id),
    onSuccess: () => {
      invalidate();
      toast.success('Task deleted');
    },
    onError: (error: Error) => toast.error(error.message),
  });

  return { create, update, move, remove };
}

export function useTemplateTasks(templateId: string | undefined) {
  return useQuery({
    queryKey: templateTasksKey(templateId ?? ''),
    queryFn: () => listTemplateTasks(templateId as string),
    enabled: Boolean(templateId),
  });
}

export function useTemplateTaskMutations(templateId: string) {
  const queryClient = useQueryClient();
  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: templateTasksKey(templateId) });

  const create = useMutation({
    mutationFn: (body: ProjectTemplateTaskBody) => createTemplateTask(templateId, body),
    onSuccess: () => {
      invalidate();
      toast.success('Checklist item added');
    },
    onError: (error: Error) => toast.error(error.message),
  });

  const update = useMutation({
    mutationFn: ({ id, body }: { id: string; body: Partial<ProjectTemplateTaskBody> }) =>
      updateTemplateTask(templateId, id, body),
    onSuccess: () => {
      invalidate();
      toast.success('Checklist item saved');
    },
    onError: (error: Error) => toast.error(error.message),
  });

  const remove = useMutation({
    mutationFn: (id: string) => deleteTemplateTask(templateId, id),
    onSuccess: () => {
      invalidate();
      toast.success('Checklist item removed');
    },
    onError: (error: Error) => toast.error(error.message),
  });

  return { create, update, remove };
}


// -------------------------------------------------- types / templates admin

/**
 * A type or template write invalidates BOTH lists.
 *
 * Deleting a type orphans nothing (the server refuses while templates exist) but
 * renaming one changes the label every template row shows, and creating a template
 * changes the type's template_count. Invalidating one list only leaves the other
 * disagreeing on screen.
 */
function useConfigInvalidate() {
  const queryClient = useQueryClient();
  return () => {
    queryClient.invalidateQueries({ queryKey: PROJECT_TYPES_KEY });
    queryClient.invalidateQueries({ queryKey: ['project-templates'] });
  };
}

export function useProjectTypeMutations() {
  const invalidate = useConfigInvalidate();

  const create = useMutation({
    mutationFn: (body: ProjectTypeBody) => createProjectType(body),
    onSuccess: () => {
      invalidate();
      toast.success('Project type created');
    },
    onError: (error: Error) => toast.error(error.message),
  });

  const update = useMutation({
    mutationFn: ({ id, body }: { id: string; body: Partial<ProjectTypeBody> }) =>
      updateProjectType(id, body),
    onSuccess: () => {
      invalidate();
      toast.success('Project type saved');
    },
    onError: (error: Error) => toast.error(error.message),
  });

  const remove = useMutation({
    mutationFn: (id: string) => deleteProjectType(id),
    onSuccess: () => {
      invalidate();
      toast.success('Project type deleted');
    },
    onError: (error: Error) => toast.error(error.message),
  });

  return { create, update, remove };
}

export function useProjectTemplateMutations() {
  const invalidate = useConfigInvalidate();

  const create = useMutation({
    mutationFn: (body: ProjectTemplateBody) => createProjectTemplate(body),
    onSuccess: () => {
      invalidate();
      toast.success('Template created');
    },
    onError: (error: Error) => toast.error(error.message),
  });

  const update = useMutation({
    mutationFn: ({ id, body }: { id: string; body: Partial<ProjectTemplateBody> }) =>
      updateProjectTemplate(id, body),
    onSuccess: () => {
      invalidate();
      toast.success('Template saved');
    },
    onError: (error: Error) => toast.error(error.message),
  });

  const remove = useMutation({
    mutationFn: (id: string) => deleteProjectTemplate(id),
    onSuccess: () => {
      invalidate();
      toast.success('Template deleted');
    },
    onError: (error: Error) => toast.error(error.message),
  });

  return { create, update, remove };
}


// ------------------------------------------------------------------- leads

export const LEADS_KEY = 'project-leads';
export const leadsListKey = (params: LeadListParams) => [LEADS_KEY, 'list', params];
export const leadKey = (leadId: string) => [LEADS_KEY, 'detail', leadId];
export const LEAD_METRICS_KEY = [LEADS_KEY, 'metrics'];
export const LEAD_REASONS_KEY = [LEADS_KEY, 'disqualify-reasons'];
export const customerPortfolioKey = (customerId: string) => [
  LEADS_KEY,
  'portfolio',
  customerId,
];

export function useLeads(params: LeadListParams) {
  return useQuery({
    ...LIST_QUERY_OPTIONS,
    queryKey: leadsListKey(params),
    queryFn: () => listLeads(params),
  });
}

/**
 * The project form's Lead picker (D4): a server search over OPEN leads only, capped at
 * 20, by title or lead code. Returned as options rather than raw leads so `ProjectForm`
 * never has to know the field is async-searched vs static.
 */
export function useFetchLeadOptions() {
  return React.useCallback(async (query: string) => {
    const result = await listLeads({ query: query || undefined, outcome: ['open'], limit: 20 });
    // Title first, code as the second line: no " · " joins (owner rule, 13 Oct).
    return result.data.map((lead) => ({
      value: lead.id,
      label: lead.title,
      description: lead.lead_code,
    }));
  }, []);
}

/** The leads list a detail URL describes, in the shape `LeadsClient` passes. */
export function leadsListParamsFromUrl(params: ListPagerParams): LeadListParams {
  return {
    query: params.searchQuery || undefined,
    outcome: params.filters.outcome ? [params.filters.outcome] : undefined,
    source: params.filters.source ? [params.filters.source] : undefined,
    page: params.pageIndex + 1,
    limit: params.pageSize,
    sort: params.sorting[0]?.id ?? 'created_at',
    dir: (params.sorting[0]?.desc ?? true ? 'desc' : 'asc') as 'asc' | 'desc',
  };
}

/** The pager's two hooks into the leads list. */
export const leadsPagerQuery = {
  listQueryKey: (params: ListPagerParams): QueryKey =>
    leadsListKey(leadsListParamsFromUrl(params)),
  fetchPage: (params: ListPagerParams): Promise<ListPagerPage> =>
    listLeads(leadsListParamsFromUrl(params)),
};

export function useLead(leadId: string | undefined) {
  return useQuery({
    queryKey: leadKey(leadId ?? ''),
    queryFn: () => getLead(leadId as string),
    enabled: Boolean(leadId),
  });
}

export function useLeadMetrics() {
  return useQuery({ queryKey: LEAD_METRICS_KEY, queryFn: () => getLeadMetrics() });
}

/**
 * The reasons are admin-configured and change about once a year, so they are cached
 * hard: refetching them on every dialog open is a request that never returns anything
 * new.
 */
export function useDisqualifyReasons() {
  return useQuery({
    queryKey: LEAD_REASONS_KEY,
    queryFn: () => listDisqualifyReasons(),
    staleTime: 10 * 60 * 1000,
  });
}

export function useCustomerPortfolio(customerId: string | undefined) {
  return useQuery({
    queryKey: customerPortfolioKey(customerId ?? ''),
    queryFn: () => getCustomerPortfolio(customerId as string),
    enabled: Boolean(customerId),
  });
}

/**
 * What qualifying would hit. Enabled only on an open lead: previewing a clash for a
 * lead that is already qualified or dead is a question nobody asked.
 */
export function useQualifyPreview(
  leadId: string | undefined,
  params: { title?: string | null; developerPartyId?: string | null; enabled?: boolean },
) {
  const title = (params.title ?? '').trim();
  return useQuery({
    ...LIST_QUERY_OPTIONS,
    queryKey: [LEADS_KEY, 'qualify-preview', leadId ?? '', title.toLowerCase(), params.developerPartyId ?? null],
    queryFn: () =>
      previewQualify(leadId as string, {
        title: title || null,
        developer_party_id: params.developerPartyId ?? null,
      }),
    enabled: Boolean(leadId) && params.enabled !== false,
  });
}

/**
 * Lead writes invalidate the PROJECT lists too.
 *
 * Qualifying creates a project, and disqualifying changes the conversion metric that
 * the pipeline header reads. Invalidating only the lead list would leave the pipeline
 * missing the project the user just created.
 */
function useLeadInvalidate() {
  const queryClient = useQueryClient();
  return (leadId?: string) => {
    queryClient.invalidateQueries({ queryKey: [LEADS_KEY] });
    queryClient.invalidateQueries({ queryKey: [PROJECTS_KEY] });
    if (leadId) queryClient.invalidateQueries({ queryKey: leadKey(leadId) });
  };
}

export function useLeadMutations() {
  const invalidate = useLeadInvalidate();

  const create = useMutation({
    mutationFn: (body: ProjectLeadBody) => createLead(body),
    onSuccess: (lead) => {
      invalidate(lead.id);
      toast.success(`${lead.lead_code} recorded`);
    },
    onError: (error: Error) => toast.error(error.message),
  });

  const update = useMutation({
    mutationFn: ({ id, body }: { id: string; body: Partial<ProjectLeadBody> }) =>
      updateLead(id, body),
    onSuccess: (lead) => {
      invalidate(lead.id);
      toast.success('Lead saved');
    },
    onError: (error: Error) => toast.error(error.message),
  });

  const move = useMutation({
    mutationFn: ({ id, toStatusId }: { id: string; toStatusId: string }) =>
      changeLeadStatus(id, toStatusId),
    onSuccess: (lead) => {
      invalidate(lead.id);
      toast.success(`${lead.lead_code} moved to ${lead.status_label ?? 'a new stage'}`);
    },
    onError: (error: Error) => toast.error(error.message),
  });

  const qualify = useMutation({
    mutationFn: ({ id, body }: { id: string; body?: LeadQualifyBody }) =>
      qualifyLead(id, body ?? {}),
    onSuccess: (project) => {
      invalidate();
      toast.success(`${project.project_code} registered from this lead`);
    },
    onError: (error: Error) => toast.error(error.message),
  });

  const disqualify = useMutation({
    mutationFn: ({ id, reason }: { id: string; reason: string }) =>
      disqualifyLead(id, reason),
    onSuccess: (lead) => {
      invalidate(lead.id);
      toast.success(`${lead.lead_code} disqualified`);
    },
    onError: (error: Error) => toast.error(error.message),
  });

  const reopen = useMutation({
    mutationFn: (id: string) => reopenLead(id),
    onSuccess: (lead) => {
      invalidate(lead.id);
      toast.success(`${lead.lead_code} reopened`);
    },
    onError: (error: Error) => toast.error(error.message),
  });

  const remove = useMutation({
    mutationFn: (id: string) => deleteLead(id),
    onSuccess: () => {
      invalidate();
      toast.success('Lead deleted');
    },
    onError: (error: Error) => toast.error(error.message),
  });

  return { create, update, move, qualify, disqualify, reopen, remove };
}


// -------------------------------------------------------------- quotations

export const QUOTATIONS_KEY = 'project-quotations';
export const quotationsKey = (projectId: string) => [QUOTATIONS_KEY, 'list', projectId];
export const versionsKey = (quotationId: string) => [QUOTATIONS_KEY, 'versions', quotationId];
export const linesKey = (versionId: string) => [QUOTATIONS_KEY, 'lines', versionId];
export const LOSS_REASONS_KEY = [QUOTATIONS_KEY, 'loss-reasons'];
export const SERIES_KEY = ['project-series'];
export const PRICE_FLOORS_KEY = ['project-price-floors'];

export function useQuotations(projectId: string | undefined) {
  return useQuery({
    queryKey: quotationsKey(projectId ?? ''),
    queryFn: () => listQuotations(projectId as string),
    enabled: Boolean(projectId),
  });
}

export function useQuotationVersions(quotationId: string | undefined) {
  return useQuery({
    queryKey: versionsKey(quotationId ?? ''),
    queryFn: () => listQuotationVersions(quotationId as string),
    enabled: Boolean(quotationId),
  });
}

/**
 * Every version of every scope on one project, flattened.
 *
 * The Quotations tab lists REVISIONS, not just the current price per scope: the client's
 * words were "in this list i can see a list of quotation for this project which includes all
 * those revisions". Nothing on the server returns them in one call, so this fans out over the
 * scopes - a handful per project - and react-query dedupes each against the per-quotation key
 * the version editor already uses, so opening a scope below the list costs no extra request.
 */
export function useProjectQuotationVersions(quotations: ProjectQuotation[] | undefined) {
  const list = quotations ?? [];
  const results = useQueries({
    queries: list.map((quotation) => ({
      queryKey: versionsKey(quotation.id),
      queryFn: () => listQuotationVersions(quotation.id),
      staleTime: 30_000,
    })),
  });

  return {
    isLoading: results.some((result) => result.isLoading),
    isError: results.some((result) => result.isError),
    error: results.find((result) => result.error)?.error ?? null,
    rows: list.flatMap((quotation, index) =>
      (results[index]?.data ?? []).map((version) => ({ quotation, version })),
    ),
  };
}

export function useQuotationLines(versionId: string | undefined) {
  return useQuery({
    queryKey: linesKey(versionId ?? ''),
    queryFn: () => listQuotationLines(versionId as string),
    enabled: Boolean(versionId),
  });
}

export function useQuotationLossReasons() {
  return useQuery({
    queryKey: LOSS_REASONS_KEY,
    queryFn: () => listQuotationLossReasons(),
    staleTime: 10 * 60 * 1000,
  });
}

export function useProjectSeries(includeInactive = false) {
  return useQuery({
    queryKey: [...SERIES_KEY, includeInactive],
    queryFn: () => listSeries(includeInactive),
  });
}

export function usePriceFloors() {
  return useQuery({ queryKey: PRICE_FLOORS_KEY, queryFn: () => listPriceFloors() });
}

/**
 * The floor in force for one product or one category.
 *
 * Keyed UNDER `PRICE_FLOORS_KEY` on purpose: every floor write invalidates that prefix,
 * so setting a category floor from the category editor also refreshes what a product
 * under it says it inherits, without either surface having to know about the other.
 */
export function useEffectivePriceFloor(
  target: { level: FloorTargetLevel; id: string } | null,
) {
  return useQuery({
    queryKey: [...PRICE_FLOORS_KEY, 'effective', target?.level, target?.id],
    queryFn: () => getEffectivePriceFloor(target as { level: FloorTargetLevel; id: string }),
    enabled: Boolean(target?.id),
  });
}

/**
 * A quotation write invalidates the PROJECT as well.
 *
 * The project's outcome is derived from its quotations (AC-E10), so winning a scope
 * changes the header, the pipeline card and the board column. Invalidating only the
 * quotation list would leave all three stating the old outcome.
 */
export function useQuotationMutations(projectId: string) {
  const queryClient = useQueryClient();
  const invalidate = (quotationId?: string) => {
    queryClient.invalidateQueries({ queryKey: [QUOTATIONS_KEY] });
    queryClient.invalidateQueries({ queryKey: projectKey(projectId) });
    queryClient.invalidateQueries({ queryKey: [PROJECTS_KEY, 'list'] });
    if (quotationId) queryClient.invalidateQueries({ queryKey: versionsKey(quotationId) });
  };

  const create = useMutation({
    mutationFn: (body: ProjectQuotationBody) => createQuotation(projectId, body),
    onSuccess: (quotation) => {
      invalidate(quotation.id);
      toast.success(`"${quotation.scope_label}" added`);
    },
    onError: (error: Error) => toast.error(error.message),
  });

  const update = useMutation({
    mutationFn: ({ id, body }: { id: string; body: Partial<ProjectQuotationBody> }) =>
      updateQuotation(id, body),
    onSuccess: (quotation) => {
      invalidate(quotation.id);
      toast.success('Quotation saved');
    },
    onError: (error: Error) => toast.error(error.message),
  });

  const revise = useMutation({
    mutationFn: (quotationId: string) => reviseQuotation(quotationId),
    onSuccess: (version) => {
      invalidate(version.quotation_id);
      toast.success(`Version ${version.version_no} opened`);
    },
    onError: (error: Error) => toast.error(error.message),
  });

  const decide = useMutation({
    mutationFn: ({ id, body }: { id: string; body: QuotationOutcomeBody }) =>
      setQuotationOutcome(id, body),
    onSuccess: (quotation) => {
      invalidate(quotation.id);
      toast.success(`"${quotation.scope_label}" marked ${quotation.outcome}`);
    },
    onError: (error: Error) => toast.error(error.message),
  });

  // No success toast here: the only caller is a ConfirmDeleteDialog, which raises its
  // own. Toasting in both places puts two notifications on screen for one delete.
  const remove = useMutation({
    mutationFn: (quotationId: string) => deleteQuotation(quotationId),
    onSuccess: () => invalidate(),
  });

  return { create, update, revise, decide, remove };
}

/**
 * Line writes invalidate the lines AND the quotation list: the list carries the version
 * total and the two alert counts, which every line edit can change.
 *
 * And the DOCUMENT, because its letterhead carries the grand total across scopes (AC-D2).
 * That is a different query from the lines, so without this a line edit moved the footer
 * under the money column and left the total at the top of the page reading the old figure -
 * two numbers on one screen disagreeing, which is exactly what makes a reader distrust the
 * arithmetic. Prefix key, so both the document list and the open document refetch.
 */
export function useQuotationLineMutations(projectId: string, versionId: string) {
  const queryClient = useQueryClient();
  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: linesKey(versionId) });
    queryClient.invalidateQueries({ queryKey: quotationsKey(projectId) });
    queryClient.invalidateQueries({ queryKey: [QUOTATIONS_KEY, 'versions'] });
    queryClient.invalidateQueries({ queryKey: quotationDocumentsKey(projectId) });
  };

  const create = useMutation({
    mutationFn: (body: QuotationLineBody) => createQuotationLine(versionId, body),
    onSuccess: (line) => {
      invalidate();
      // Only NEWS gets a toast. A line that saved exactly as typed is not news: the row
      // already shows the value, and a toast per blur moves the page under the cursor
      // while somebody is working down a column of twenty lines.
      if (line.is_below_floor) {
        toast.warning(
          `Below the floor of ${line.floor_value_applied}. Management has been notified.`,
        );
      }
    },
    onError: (error: Error) => toast.error(error.message),
  });

  const update = useMutation({
    mutationFn: ({ id, body }: { id: string; body: Partial<QuotationLineBody> }) =>
      updateQuotationLine(versionId, id, body),
    onSuccess: (line) => {
      invalidate();
      if (line.is_below_floor) {
        toast.warning(`Below the floor of ${line.floor_value_applied}.`);
      }
    },
    onError: (error: Error) => toast.error(error.message),
  });

  const remove = useMutation({
    mutationFn: (lineId: string) => deleteQuotationLine(versionId, lineId),
    onSuccess: () => {
      invalidate();
      toast.success('Line removed');
    },
    onError: (error: Error) => toast.error(error.message),
  });

  return { create, update, remove };
}

/**
 * The whole line set of one version in a single write, for the edit view's Save (S10/S11).
 *
 * Separate from `useQuotationLineMutations` because the version is a PAYLOAD field here, not a
 * binding: one Save can cover several scopes, and a hook bound to one version cannot answer for
 * the others. The invalidations are the same set for the same reason - the quotation list carries
 * the version total, and the document's letterhead carries the grand total across scopes.
 */
export function useQuotationBulkLineMutation(projectId: string) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ versionId, lines }: { versionId: string; lines: QuotationLineBulkItem[] }) =>
      replaceQuotationLines(versionId, lines),
    onSuccess: (_lines, { versionId }) => {
      queryClient.invalidateQueries({ queryKey: linesKey(versionId) });
      queryClient.invalidateQueries({ queryKey: quotationsKey(projectId) });
      queryClient.invalidateQueries({ queryKey: [QUOTATIONS_KEY, 'versions'] });
      queryClient.invalidateQueries({ queryKey: quotationDocumentsKey(projectId) });
    },
    // No toast: the screen raises ONE for the whole Save, and a quotation with four scopes
    // would otherwise stack four notifications for a single button press.
    onError: (error: Error) => toast.error(error.message),
  });
}

export function useSeriesMutations() {
  const queryClient = useQueryClient();
  const invalidate = () => queryClient.invalidateQueries({ queryKey: SERIES_KEY });

  const create = useMutation({
    mutationFn: (body: ProjectSeriesBody) => createSeries(body),
    onSuccess: () => {
      invalidate();
      toast.success('Series created');
    },
    onError: (error: Error) => toast.error(error.message),
  });

  const update = useMutation({
    mutationFn: ({ id, body }: { id: string; body: Partial<ProjectSeriesBody> }) =>
      updateSeries(id, body),
    onSuccess: () => {
      invalidate();
      toast.success('Series saved');
    },
    onError: (error: Error) => toast.error(error.message),
  });

  const remove = useMutation({
    mutationFn: (id: string) => deleteSeries(id),
    onSuccess: (_data, id) => {
      // DROP this series' product rows rather than invalidating them.
      //
      // The rows query is keyed `[...SERIES_KEY, 'rows', id]`, so the blanket invalidate
      // below matches it by prefix and REFETCHES a series that no longer exists - a 404 in
      // the console on an operation that succeeded perfectly. Removing the entry first
      // leaves nothing to refetch.
      queryClient.removeQueries({ queryKey: [...SERIES_KEY, 'rows', id] });
      invalidate();
      toast.success('Series deleted');
    },
    onError: (error: Error) => toast.error(error.message),
  });

  return { create, update, remove };
}

/**
 * Loading a list of product codes onto a series (S18), pasted or off a file.
 *
 * NO success toast. The whole answer is the report - how many matched, how many were
 * already there, and above all which codes the catalogue does not carry - and the screen
 * renders it. A toast saying "imported" over a result that names 49 misses would be the
 * one sentence the reader trusts and the wrong one.
 */
/**
 * The products on one series, with what the series sells them for (T2).
 *
 * Keyed UNDER `SERIES_KEY` so that every series write - including a sheet import, which
 * changes both the membership and the prices - refetches this table without each mutation
 * having to remember it exists.
 */
export function useSeriesProductRows(
  seriesId: string | undefined,
  params: { page: number; limit: number; query?: string },
) {
  return useQuery({
    ...LIST_QUERY_OPTIONS,
    queryKey: [...SERIES_KEY, 'rows', seriesId, params],
    queryFn: () => getSeriesProductRows(seriesId as string, params),
    enabled: Boolean(seriesId),
  });
}

/** Every priced row of the series, for the quotation line's opening price. */
export function useSeriesPricedRows(seriesId?: string) {
  return useQuery({
    queryKey: [...SERIES_KEY, 'priced', seriesId],
    queryFn: () => getSeriesPricedRows(seriesId as string),
    enabled: Boolean(seriesId),
  });
}

export function useSeriesProductRowMutations(seriesId?: string) {
  const queryClient = useQueryClient();
  const invalidate = () => queryClient.invalidateQueries({ queryKey: SERIES_KEY });

  const setPricing = useMutation({
    mutationFn: ({
      productId,
      body,
    }: {
      productId: string;
      body: { selling_price: string | null; max_discount_pct: string | null };
    }) => updateSeriesProductPricing(seriesId as string, productId, body),
    onSuccess: invalidate,
    onError: (error: Error) => toast.error(error.message),
  });

  const remove = useMutation({
    mutationFn: (productId: string) => removeSeriesProduct(seriesId as string, productId),
    onSuccess: () => {
      invalidate();
      toast.success('Product removed from the series');
    },
    onError: (error: Error) => toast.error(error.message),
  });

  return { setPricing, remove };
}

export function useSeriesSetRows(seriesId?: string) {
  return useQuery({
    queryKey: [...SERIES_KEY, 'set-rows', seriesId],
    queryFn: () => getSeriesSetRows(seriesId as string),
    enabled: Boolean(seriesId),
  });
}

export function useSeriesSetRowMutations(seriesId?: string) {
  const queryClient = useQueryClient();
  const invalidate = () => queryClient.invalidateQueries({ queryKey: SERIES_KEY });

  const setPricing = useMutation({
    mutationFn: ({
      productSetId,
      body,
    }: {
      productSetId: string;
      body: { selling_price: string | null; max_discount_pct: string | null };
    }) => updateSeriesSetPricing(seriesId as string, productSetId, body),
    onSuccess: invalidate,
    onError: (error: Error) => toast.error(error.message),
  });

  const remove = useMutation({
    mutationFn: (productSetId: string) => removeSeriesSet(seriesId as string, productSetId),
    onSuccess: () => {
      invalidate();
      toast.success('Set removed from the series');
    },
    onError: (error: Error) => toast.error(error.message),
  });

  return { setPricing, remove };
}

export function useSeriesProductMutations() {
  const queryClient = useQueryClient();
  const invalidate = () => queryClient.invalidateQueries({ queryKey: SERIES_KEY });

  const importCodes = useMutation({
    mutationFn: ({ id, body }: { id: string; body: SeriesProductImportBody }) =>
      importSeriesProducts(id, body),
    onSuccess: invalidate,
    onError: (error: Error) => toast.error(error.message),
  });

  // Answers a JOB, not a report. The invalidate here is deliberately absent: nothing has
  // been written yet when this resolves, and refreshing the products table at that moment
  // would redraw the same rows and look like the import did nothing.
  const importFile = useMutation({
    mutationFn: ({
      id,
      file,
      mode,
    }: {
      id: string;
      file: File;
      mode: SeriesProductImportBody['mode'];
    }) => uploadSeriesProducts(id, file, mode),
    onError: (error: Error) => toast.error(error.message),
  });

  return { importCodes, importFile, invalidateSeries: invalidate };
}

/**
 * Watch a queued sheet load until it stops.
 *
 * Polling rather than a socket because the whole conversation is three or four messages
 * long and the repo has no socket for anything else - a first one for this would be
 * infrastructure nobody else uses.
 *
 * Two seconds: fast enough that a small paste-sized file feels immediate, slow enough that
 * a 9 MB workbook taking half a minute is not thirty requests. `refetchInterval` returns
 * `false` once the job reaches a terminal state, so a finished import stops polling without
 * the caller having to remember to unmount anything.
 */
export function useImportJobStatus(jobId: string | null) {
  return useQuery({
    queryKey: ['import-job-status', jobId],
    enabled: Boolean(jobId),
    queryFn: () => getImportJobStatus(jobId as string),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      if (!status) return 2000;
      return importJobPhase(status) === 'running' ? 2000 : false;
    },
    // The answer is a fact about a job, never stale in the react-query sense: the interval
    // above is the only thing that should decide when to ask again.
    staleTime: 0,
    gcTime: 0,
  });
}

/**
 * Re-ask the guardrails on one version against today's master data (S19).
 *
 * Invalidates the same set a line write does: the flags live ON the lines, and the
 * quotation list and the document letterhead both carry the alert counts, so a recompute
 * that refreshed only the line table would leave two surfaces disagreeing about how many
 * lines are non-standard.
 *
 * No toast here either - the caller renders the report, and "6 lines are no longer
 * non-standard" is not something a disappearing notification should be the only record of.
 */
/**
 * The verdict for one DRAFT line, kept current as it is typed.
 *
 * Debounced at 400ms: judging fires a request, and a keystroke-per-request would hammer
 * the API for answers about prices nobody has finished typing. The debounce lives HERE,
 * on the queried value, so every caller gets the same behaviour for free.
 *
 * `LIST_QUERY_OPTIONS`'s `placeholderData` keeps the previous verdict on screen while the next loads - a badge
 * that blinks off and on with every keystroke reads as the system changing its mind.
 *
 * Verdicts are judged by the server (the same `is_in_series` / `resolve_floor` the save
 * runs); this hook only decides WHEN to ask.
 */
export function useLineVerdict(
  quotationId: string,
  draft: { product_id?: string; product_set_id?: string; unit_price?: string },
  enabled: boolean,
) {
  const [settled, setSettled] = React.useState(draft);
  React.useEffect(() => {
    const handle = setTimeout(() => setSettled(draft), 400);
    return () => clearTimeout(handle);
    // Keyed on the VALUES, not the object: the caller builds a fresh literal per render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft.product_id, draft.product_set_id, draft.unit_price]);

  // Ask only once the debounce has CAUGHT UP with what is on screen. Without this, the
  // instant a draft diverges (`enabled` flips true) the query would fire against the
  // still-settling PREVIOUS values - judging the old product and briefly showing its
  // verdict against the new one, which is the exact confident-wrong-answer this hook
  // exists to prevent.
  const caughtUp =
    (settled.product_id ?? '') === (draft.product_id ?? '') &&
    (settled.product_set_id ?? '') === (draft.product_set_id ?? '') &&
    (settled.unit_price ?? '') === (draft.unit_price ?? '');

  return useQuery({
    ...LIST_QUERY_OPTIONS,
    queryKey: [
      'quotation-line-verdict',
      quotationId,
      settled.product_id ?? '',
      settled.product_set_id ?? '',
      settled.unit_price ?? '',
    ],
    queryFn: () =>
      judgeQuotationLine(quotationId, {
        product_id: settled.product_id,
        product_set_id: settled.product_set_id,
        unit_price: settled.unit_price,
      }),
    enabled: enabled && caughtUp,
    // The same draft judged twice in 30s gets the cached answer - typing a price, deleting
    // it, and retyping it should not be three requests.
    staleTime: 30_000,
  });
}

/**
 * A product's photo and drawing cells for a line that has no stored answer for it yet (a new line,
 * or one whose product was just changed). Null asks nothing.
 */
export function useQuotationLineImages(productId: string | null) {
  return useQuery({
    queryKey: ['quotation-line-images', productId ?? ''],
    queryFn: () => getQuotationLineImages(productId as string),
    enabled: Boolean(productId),
    // The signed URLs are reused server-side for 45 minutes; a few minutes here is well inside.
    staleTime: 5 * 60_000,
  });
}

/** One set as a quotation line reads it (complete sets, price, parts), by id; null asks nothing. */
export function useProductSetLineRef(setId: string | null) {
  return useQuery({
    queryKey: ['product-set-line-ref', setId ?? ''],
    queryFn: async () => (await getProductSetsForLineSelect({ ids: [setId as string] }))[0] ?? null,
    enabled: Boolean(setId),
    staleTime: 60_000,
  });
}

export function useQuotationRecomputeMutation(projectId: string) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (versionId: string) => recomputeQuotationVersion(versionId),
    onSuccess: (_result, versionId) => {
      queryClient.invalidateQueries({ queryKey: linesKey(versionId) });
      queryClient.invalidateQueries({ queryKey: quotationsKey(projectId) });
      queryClient.invalidateQueries({ queryKey: [QUOTATIONS_KEY, 'versions'] });
      queryClient.invalidateQueries({ queryKey: quotationDocumentsKey(projectId) });
    },
    onError: (error: Error) => toast.error(error.message),
  });
}

export function usePriceFloorMutations() {
  const queryClient = useQueryClient();
  const invalidate = () => queryClient.invalidateQueries({ queryKey: PRICE_FLOORS_KEY });

  const upsert = useMutation({
    mutationFn: (body: PriceFloorRuleBody) => upsertPriceFloor(body),
    onSuccess: () => {
      invalidate();
      toast.success('Price floor saved');
    },
    onError: (error: Error) => toast.error(error.message),
  });

  const remove = useMutation({
    mutationFn: (id: string) => deletePriceFloor(id),
    onSuccess: () => {
      invalidate();
      toast.success('Price floor removed');
    },
    onError: (error: Error) => toast.error(error.message),
  });

  return { upsert, remove };
}

// ------------------------------------------------------- samples and customer POs

export const SAMPLES_KEY = 'project-samples';
export const POS_KEY = 'project-purchase-orders';
export const samplesKey = (projectId: string) => [SAMPLES_KEY, projectId];
export const poLinesKey = (poId: string) => [POS_KEY, 'lines', poId];

export function useSamples(projectId: string | undefined) {
  return useQuery({
    queryKey: samplesKey(projectId ?? ''),
    queryFn: () => listSamples(projectId as string),
    enabled: Boolean(projectId),
  });
}

export function usePurchaseOrders(projectId: string | undefined) {
  return useQuery({
    queryKey: [POS_KEY, projectId],
    queryFn: () => listPurchaseOrders(projectId as string),
    enabled: Boolean(projectId),
  });
}


export function usePurchaseOrderLines(poId: string | undefined) {
  return useQuery({
    queryKey: poLinesKey(poId ?? ''),
    queryFn: () => listPurchaseOrderLines(poId as string),
    enabled: Boolean(poId),
  });
}

/**
 * A sample write invalidates the QUOTATIONS too: the version a sample hangs off shows a
 * sample count, so recording one changes a panel the user may be looking at.
 */
export function useSampleMutations(projectId: string) {
  const queryClient = useQueryClient();
  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: samplesKey(projectId) });
    queryClient.invalidateQueries({ queryKey: quotationsKey(projectId) });
  };

  const create = useMutation({
    mutationFn: (body: ProjectSampleBody) => createSample(projectId, body),
    onSuccess: () => {
      invalidate();
      toast.success('Sample recorded');
    },
    onError: (error: Error) => toast.error(error.message),
  });

  const update = useMutation({
    mutationFn: ({ id, body }: { id: string; body: Partial<ProjectSampleBody> }) =>
      updateSample(id, body),
    onSuccess: () => {
      invalidate();
      toast.success('Sample saved');
    },
    onError: (error: Error) => toast.error(error.message),
  });

  // No success toast: the only caller is a ConfirmDeleteDialog, which raises its own.
  const remove = useMutation({
    mutationFn: (sampleId: string) => deleteSample(sampleId),
    onSuccess: () => invalidate(),
  });

  return { create, update, remove };
}

/**
 * A PO write invalidates the PROJECT as well: the first PO moves the funnel to PO
 * Received (AC-F10), so the header and the board column change with it.
 */
export function usePurchaseOrderMutations(projectId: string) {
  const queryClient = useQueryClient();
  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: [POS_KEY, projectId] });
    queryClient.invalidateQueries({ queryKey: projectKey(projectId) });
    queryClient.invalidateQueries({ queryKey: [PROJECTS_KEY, 'list'] });
  };

  const create = useMutation({
    mutationFn: (body: ProjectPurchaseOrderBody) => createPurchaseOrder(projectId, body),
    onSuccess: (po) => {
      invalidate();
      // Two different truths, and the user needs to know which one happened: the PO is
      // always recorded, the funnel move is not always legal from where the project sits.
      if (po.status_moved_to_po_received) {
        toast.success(`${po.po_number} recorded. The project moved to PO Received.`);
      } else {
        toast.success(`${po.po_number} recorded.`);
      }
    },
    onError: (error: Error) => toast.error(error.message),
  });

  /**
   * The PO's header and, when the body carries `lines`, its whole line set. One request, so
   * the edit view's Save either lands or does not.
   *
   * The LINES query is invalidated too, and not only when lines were sent: re-binding the
   * quotation version rechecks every line's mismatch flags on the server, so a header-only
   * save can change every row on screen. Leaving them cached is what showed "not quoted"
   * beside lines the save had just matched.
   */
  const update = useMutation({
    mutationFn: ({ id, body }: { id: string; body: ProjectPurchaseOrderSaveBody }) =>
      updatePurchaseOrder(id, body),
    onSuccess: (_po, { id }) => {
      invalidate();
      queryClient.invalidateQueries({ queryKey: poLinesKey(id) });
      toast.success('Purchase order saved');
    },
    onError: (error: Error) => toast.error(error.message),
  });

  const remove = useMutation({
    mutationFn: (poId: string) => deletePurchaseOrder(poId),
    onSuccess: () => invalidate(),
  });

  return { create, update, remove };
}

export function usePurchaseOrderLineMutations(projectId: string, poId: string) {
  const queryClient = useQueryClient();
  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: poLinesKey(poId) });
    queryClient.invalidateQueries({ queryKey: [POS_KEY, projectId] });
  };

  const create = useMutation({
    mutationFn: (body: PurchaseOrderLineBody) => createPurchaseOrderLine(poId, body),
    onSuccess: (line) => {
      invalidate();
      // A mismatch is recorded, never refused (AC-F9), so this is information rather
      // than an error -- but it has to be said out loud or nobody looks.
      if (line.model_mismatch) {
        toast.warning('Added. This item is not on the quoted version.');
      } else if (line.price_mismatch) {
        toast.warning(`Added. Quoted at ${line.quoted_unit_price}, ordered at ${line.unit_price}.`);
      }
    },
    onError: (error: Error) => toast.error(error.message),
  });

  const update = useMutation({
    mutationFn: ({ id, body }: { id: string; body: Partial<PurchaseOrderLineBody> }) =>
      updatePurchaseOrderLine(poId, id, body),
    onSuccess: () => {
      // Silent, like the quotation editor: a routine save is not news (see create above).
      invalidate();
    },
    onError: (error: Error) => toast.error(error.message),
  });

  const remove = useMutation({
    mutationFn: (lineId: string) => deletePurchaseOrderLine(poId, lineId),
    onSuccess: () => invalidate(),
  });

  return { create, update, remove };
}

// ------------------------------------------------------------- sponsorship link

export const SPONSORSHIPS_KEY = 'project-sponsorships';

export function useProjectSponsorships(projectId: string | undefined) {
  return useQuery({
    queryKey: [SPONSORSHIPS_KEY, projectId],
    queryFn: () => listProjectSponsorships(projectId as string),
    enabled: Boolean(projectId),
  });
}

export function useSponsorshipRollup(projectId: string | undefined) {
  return useQuery({
    queryKey: [SPONSORSHIPS_KEY, 'rollup', projectId],
    queryFn: () => getSponsorshipRollup(projectId as string),
    enabled: Boolean(projectId),
  });
}

// ------------------------------------------------------------------- reporting

export function useProjectDashboard() {
  return useQuery({
    queryKey: ['project-dashboard'],
    queryFn: () => getProjectDashboard(),
    staleTime: 60_000,
  });
}
