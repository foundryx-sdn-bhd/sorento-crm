import { apiFetch } from '@/lib/api';
import { extractApiError } from '@/lib/api-client';
import type {
  ClashPreview,
  Project,
  ProjectCollaborator,
  ProjectListParams,
  ProjectParty,
  ProjectPartyBody,
  ProjectRegisterBody,
  ProjectStakeholder,
  ProjectStakeholderBody,
  ProjectTemplate,
  ProjectType,
  ProjectTypeBody,
  ProjectTask,
  ProjectTaskBody,
  ProjectTemplateBody,
  CustomerPortfolio,
  LeadConversionMetrics,
  LeadListParams,
  LeadQualifyBody,
  LeadReasonOption,
  EffectivePriceFloor,
  FloorTargetLevel,
  PriceFloorRule,
  PriceFloorRuleBody,
  ProjectDashboard,
  ProjectSponsorship,
  SponsorshipRollup,
  ProjectSample,
  ProjectSampleBody,
  ProjectPurchaseOrder,
  ProjectPurchaseOrderBody,
  ProjectPurchaseOrderSaveBody,
  PurchaseOrderLine,
  PurchaseOrderLineBody,
  ProjectLead,
  ProjectLeadBody,
  ProjectQuotation,
  ProjectQuotationBody,
  ProjectSeries,
  ProjectSeriesBody,
  QuotationLine,
  QuotationLineBody,
  QuotationLineBulkItem,
  QuotationOutcomeBody,
  QuotationRecomputeResult,
  QuotationVersion,
  ImportJobStatus,
  QuotationLineImages,
  QuotationLineVerdict,
  SeriesProductImportBody,
  SeriesProductImportJob,
  SeriesProductImportResult,
  SeriesProductRow,
  ProjectTemplateTask,
  ProjectTemplateTaskBody,
  ProjectUpdateBody,
  TakeoverRequest,
  TaskHistoryEntry,
  TaskPhase,
  TaskStatusChangeBody,
} from '../types/project.types';

const BASE = '/api/v1/project-sales';

interface ListEnvelope<T> {
  data: T[];
  pagination: { total: number; page: number; limit: number };
  empty: boolean;
}

/** Appends repeated params for array filters, which is what the API expects. */
function buildProjectParams(params: ProjectListParams): string {
  const sp = new URLSearchParams();
  if (params.query) sp.set('query', params.query);
  if (params.only_critical) sp.set('only_critical', 'true');
  if (params.page) sp.set('page', String(params.page));
  if (params.limit) sp.set('limit', String(params.limit));
  if (params.sort) sp.set('sort', params.sort);
  if (params.dir) sp.set('dir', params.dir);
  (
    ['status_id', 'outcome', 'owner_user_id', 'developer_party_id', 'type_id', 'brand_id'] as const
  ).forEach((key) => {
    (params[key] ?? []).forEach((value) => sp.append(key, value));
  });
  const query = sp.toString();
  return query ? `?${query}` : '';
}

export async function listProjects(
  params: ProjectListParams = {},
): Promise<ListEnvelope<Project>> {
  const response = await apiFetch(`${BASE}/projects/${buildProjectParams(params)}`);
  if (!response.ok) throw new Error(await extractApiError(response, 'Failed to load projects'));
  return response.json();
}

/** `GET /projects/` takes `limit` up to `MAX_PAGE_LIMIT` (`app/schemas/common.py`, backend). */
const PROJECTS_MAX_PAGE_LIMIT = 1000;

/**
 * Options for the "which project" picker on the page-level Start menu (S2-6): only
 * projects the user can edit, since that is what the upload endpoints require anyway.
 *
 * `can_edit` is filtered client-side, so a page of the newest projects can be all non-editable
 * ones while an editable project sits on a later page - paging through with the backend's own
 * max page size until every row is fetched is what keeps the picker from missing one (Should
 * fix 2, PR #1219 round 1).
 */
export async function listEditableProjectOptions(query: string) {
  const projects: Project[] = [];
  let page = 1;
  for (;;) {
    const body = await listProjects({
      query: query || undefined,
      page,
      limit: PROJECTS_MAX_PAGE_LIMIT,
    });
    projects.push(...body.data);
    if (body.data.length < PROJECTS_MAX_PAGE_LIMIT || projects.length >= body.pagination.total) {
      break;
    }
    page += 1;
  }
  return projects
    .filter((project) => project.can_edit)
    .map((project) => ({
      value: project.id,
      label: project.title,
      description: [project.project_code, project.developer_name].filter(Boolean).join(' · '),
    }));
}

export async function getProject(projectId: string): Promise<Project> {
  const response = await apiFetch(`${BASE}/projects/${projectId}`);
  if (!response.ok)
    throw new Error(await extractApiError(response, 'Failed to load this project'));
  return response.json();
}

/**
 * Checks a title while the user is still typing it.
 *
 * Deliberately separate from the create call: warning at submit time, after the form
 * is filled, is what teaches people to work around the check.
 */
export async function previewClashes(body: {
  title: string;
  developer_party_id?: string | null;
}): Promise<ClashPreview> {
  const response = await apiFetch(`${BASE}/projects/clash-preview`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!response.ok)
    throw new Error(await extractApiError(response, 'Could not check for existing projects'));
  return response.json();
}

export async function registerProject(body: ProjectRegisterBody): Promise<Project> {
  const response = await apiFetch(`${BASE}/projects/`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!response.ok)
    throw new Error(await extractApiError(response, 'Failed to register the project'));
  return response.json();
}

export async function updateProject(
  projectId: string,
  body: ProjectUpdateBody,
): Promise<Project> {
  const response = await apiFetch(`${BASE}/projects/${projectId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!response.ok)
    throw new Error(await extractApiError(response, 'Failed to save the project'));
  return response.json();
}

export async function changeProjectStatus(
  projectId: string,
  toStatusId: string,
): Promise<Project> {
  const response = await apiFetch(`${BASE}/projects/${projectId}/status`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ to_status_id: toStatusId }),
  });
  if (!response.ok)
    throw new Error(await extractApiError(response, 'Failed to move the project'));
  return response.json();
}

export async function deleteProject(projectId: string): Promise<void> {
  const response = await apiFetch(`${BASE}/projects/${projectId}`, { method: 'DELETE' });
  if (!response.ok)
    throw new Error(await extractApiError(response, 'Failed to delete the project'));
}

// ------------------------------------------------------------- stakeholders

export async function listStakeholders(projectId: string): Promise<ProjectStakeholder[]> {
  const response = await apiFetch(`${BASE}/projects/${projectId}/stakeholders`);
  if (!response.ok)
    throw new Error(await extractApiError(response, 'Failed to load stakeholders'));
  const body: ListEnvelope<ProjectStakeholder> = await response.json();
  return body.data;
}

export async function addStakeholder(
  projectId: string,
  body: ProjectStakeholderBody,
): Promise<ProjectStakeholder> {
  const response = await apiFetch(`${BASE}/projects/${projectId}/stakeholders`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!response.ok)
    throw new Error(await extractApiError(response, 'Failed to add the stakeholder'));
  return response.json();
}

export async function updateStakeholder(
  projectId: string,
  stakeholderId: string,
  body: Partial<ProjectStakeholderBody>,
): Promise<ProjectStakeholder> {
  const response = await apiFetch(
    `${BASE}/projects/${projectId}/stakeholders/${stakeholderId}`,
    {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    },
  );
  if (!response.ok)
    throw new Error(await extractApiError(response, 'Failed to save the stakeholder'));
  return response.json();
}

export async function removeStakeholder(
  projectId: string,
  stakeholderId: string,
): Promise<void> {
  const response = await apiFetch(
    `${BASE}/projects/${projectId}/stakeholders/${stakeholderId}`,
    { method: 'DELETE' },
  );
  if (!response.ok)
    throw new Error(await extractApiError(response, 'Failed to remove the stakeholder'));
}

// ------------------------------------------------- collaborators / requests

export async function listCollaborators(projectId: string): Promise<ProjectCollaborator[]> {
  const response = await apiFetch(`${BASE}/projects/${projectId}/collaborators`);
  if (!response.ok)
    throw new Error(await extractApiError(response, 'Failed to load collaborators'));
  const body: ListEnvelope<ProjectCollaborator> = await response.json();
  return body.data;
}

export async function listTakeoverRequests(projectId: string): Promise<TakeoverRequest[]> {
  const response = await apiFetch(`${BASE}/projects/${projectId}/takeover-requests`);
  if (!response.ok) throw new Error(await extractApiError(response, 'Failed to load requests'));
  const body: ListEnvelope<TakeoverRequest> = await response.json();
  return body.data;
}

export async function createTakeoverRequest(
  projectId: string,
  body: { kind: 'join' | 'dispute'; reason: string },
): Promise<TakeoverRequest> {
  const response = await apiFetch(`${BASE}/projects/${projectId}/takeover-requests`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!response.ok)
    throw new Error(await extractApiError(response, 'Failed to send the request'));
  return response.json();
}

export async function decideTakeoverRequest(
  projectId: string,
  requestId: string,
  body: { approve: boolean; decision_note?: string | null },
): Promise<TakeoverRequest> {
  const response = await apiFetch(
    `${BASE}/projects/${projectId}/takeover-requests/${requestId}/decide`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    },
  );
  if (!response.ok)
    throw new Error(await extractApiError(response, 'Failed to record the decision'));
  return response.json();
}

// ------------------------------------------------------------------ parties

export async function listParties(
  params: {
    party_type?: string;
    query?: string;
    include_inactive?: boolean;
    page?: number;
    limit?: number;
  } = {},
): Promise<ListEnvelope<ProjectParty>> {
  const sp = new URLSearchParams();
  if (params.party_type) sp.set('party_type', params.party_type);
  if (params.query) sp.set('query', params.query);
  if (params.include_inactive) sp.set('include_inactive', 'true');
  if (params.page) sp.set('page', String(params.page));
  if (params.limit) sp.set('limit', String(params.limit));
  const query = sp.toString();
  const response = await apiFetch(`${BASE}/parties/${query ? `?${query}` : ''}`);
  if (!response.ok) throw new Error(await extractApiError(response, 'Failed to load parties'));
  return response.json();
}

export async function createParty(body: ProjectPartyBody): Promise<ProjectParty> {
  const response = await apiFetch(`${BASE}/parties/`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error(await extractApiError(response, 'Failed to create the party'));
  return response.json();
}

export async function updateParty(
  partyId: string,
  body: Partial<ProjectPartyBody>,
): Promise<ProjectParty> {
  const response = await apiFetch(`${BASE}/parties/${partyId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error(await extractApiError(response, 'Failed to save the party'));
  return response.json();
}

export async function deleteParty(partyId: string): Promise<void> {
  const response = await apiFetch(`${BASE}/parties/${partyId}`, { method: 'DELETE' });
  if (!response.ok) throw new Error(await extractApiError(response, 'Failed to delete the party'));
}

// ------------------------------------------------------- types / templates

export async function listProjectTypes(includeInactive = false): Promise<ProjectType[]> {
  const response = await apiFetch(
    `${BASE}/config/types${includeInactive ? '?include_inactive=true' : ''}`,
  );
  if (!response.ok)
    throw new Error(await extractApiError(response, 'Failed to load project types'));
  const body: ListEnvelope<ProjectType> = await response.json();
  return body.data;
}

export async function listProjectTemplates(typeId?: string): Promise<ProjectTemplate[]> {
  const sp = new URLSearchParams();
  if (typeId) sp.set('type_id', typeId);
  const query = sp.toString();
  const response = await apiFetch(`${BASE}/config/templates${query ? `?${query}` : ''}`);
  if (!response.ok) throw new Error(await extractApiError(response, 'Failed to load templates'));
  const body: ListEnvelope<ProjectTemplate> = await response.json();
  return body.data;
}

export async function createProjectType(body: ProjectTypeBody): Promise<ProjectType> {
  const response = await apiFetch(`${BASE}/config/types`, {
    method: 'POST',
    body: JSON.stringify(body),
  });
  if (!response.ok)
    throw new Error(await extractApiError(response, 'Failed to create the project type'));
  return response.json();
}

export async function updateProjectType(
  typeId: string,
  body: Partial<ProjectTypeBody>,
): Promise<ProjectType> {
  const response = await apiFetch(`${BASE}/config/types/${typeId}`, {
    method: 'PUT',
    body: JSON.stringify(body),
  });
  if (!response.ok)
    throw new Error(await extractApiError(response, 'Failed to save the project type'));
  return response.json();
}

export async function deleteProjectType(typeId: string): Promise<void> {
  const response = await apiFetch(`${BASE}/config/types/${typeId}`, { method: 'DELETE' });
  if (!response.ok)
    throw new Error(await extractApiError(response, 'Failed to delete the project type'));
}

export async function createProjectTemplate(
  body: ProjectTemplateBody,
): Promise<ProjectTemplate> {
  const response = await apiFetch(`${BASE}/config/templates`, {
    method: 'POST',
    body: JSON.stringify(body),
  });
  if (!response.ok)
    throw new Error(await extractApiError(response, 'Failed to create the template'));
  return response.json();
}

export async function updateProjectTemplate(
  templateId: string,
  body: Partial<ProjectTemplateBody>,
): Promise<ProjectTemplate> {
  const response = await apiFetch(`${BASE}/config/templates/${templateId}`, {
    method: 'PUT',
    body: JSON.stringify(body),
  });
  if (!response.ok)
    throw new Error(await extractApiError(response, 'Failed to save the template'));
  return response.json();
}

export async function deleteProjectTemplate(templateId: string): Promise<void> {
  const response = await apiFetch(`${BASE}/config/templates/${templateId}`, {
    method: 'DELETE',
  });
  if (!response.ok)
    throw new Error(await extractApiError(response, 'Failed to delete the template'));
}

// ------------------------------------------------------------------- tasks

export async function listProjectTasks(
  projectId: string,
  taskPhase?: TaskPhase,
): Promise<ProjectTask[]> {
  const sp = new URLSearchParams();
  if (taskPhase) sp.set('task_phase', taskPhase);
  const query = sp.toString();
  const response = await apiFetch(
    `${BASE}/projects/${projectId}/tasks${query ? `?${query}` : ''}`,
  );
  if (!response.ok) throw new Error(await extractApiError(response, 'Failed to load tasks'));
  const body: ListEnvelope<ProjectTask> = await response.json();
  return body.data;
}

export async function createProjectTask(
  projectId: string,
  body: ProjectTaskBody,
): Promise<ProjectTask> {
  const response = await apiFetch(`${BASE}/projects/${projectId}/tasks`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error(await extractApiError(response, 'Failed to add the task'));
  return response.json();
}

export async function updateProjectTask(
  projectId: string,
  taskId: string,
  body: Partial<ProjectTaskBody>,
): Promise<ProjectTask> {
  const response = await apiFetch(`${BASE}/projects/${projectId}/tasks/${taskId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error(await extractApiError(response, 'Failed to save the task'));
  return response.json();
}

/**
 * The move and its required context go in ONE request.
 *
 * Escalate needs a person and Stuck needs a reason; the server rejects the move
 * without them, so splitting this into two calls would leave a window where the task
 * is escalated to nobody.
 */
export async function changeTaskStatus(
  projectId: string,
  taskId: string,
  body: TaskStatusChangeBody,
): Promise<ProjectTask> {
  const response = await apiFetch(`${BASE}/projects/${projectId}/tasks/${taskId}/status`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error(await extractApiError(response, 'Failed to move the task'));
  return response.json();
}

export async function deleteProjectTask(projectId: string, taskId: string): Promise<void> {
  const response = await apiFetch(`${BASE}/projects/${projectId}/tasks/${taskId}`, {
    method: 'DELETE',
  });
  if (!response.ok) throw new Error(await extractApiError(response, 'Failed to delete the task'));
}

export async function getTaskHistory(
  projectId: string,
  taskId: string,
): Promise<TaskHistoryEntry[]> {
  const response = await apiFetch(
    `${BASE}/projects/${projectId}/tasks/${taskId}/history`,
  );
  if (!response.ok)
    throw new Error(await extractApiError(response, 'Failed to load the task history'));
  const body: ListEnvelope<TaskHistoryEntry> = await response.json();
  return body.data;
}

export async function listMyTasks(
  params: { include_unassigned_owned?: boolean; page?: number; limit?: number } = {},
): Promise<ListEnvelope<ProjectTask>> {
  const sp = new URLSearchParams();
  if (params.include_unassigned_owned) sp.set('include_unassigned_owned', 'true');
  if (params.page) sp.set('page', String(params.page));
  if (params.limit) sp.set('limit', String(params.limit));
  const query = sp.toString();
  const response = await apiFetch(`${BASE}/my-tasks${query ? `?${query}` : ''}`);
  if (!response.ok) throw new Error(await extractApiError(response, 'Failed to load your tasks'));
  return response.json();
}

// ------------------------------------------------ template checklist admin

export async function listTemplateTasks(
  templateId: string,
): Promise<ProjectTemplateTask[]> {
  const response = await apiFetch(`${BASE}/config/templates/${templateId}/tasks`);
  if (!response.ok)
    throw new Error(await extractApiError(response, 'Failed to load the checklist'));
  const body: ListEnvelope<ProjectTemplateTask> = await response.json();
  return body.data;
}

export async function createTemplateTask(
  templateId: string,
  body: ProjectTemplateTaskBody,
): Promise<ProjectTemplateTask> {
  const response = await apiFetch(`${BASE}/config/templates/${templateId}/tasks`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!response.ok)
    throw new Error(await extractApiError(response, 'Failed to add the checklist item'));
  return response.json();
}

export async function updateTemplateTask(
  templateId: string,
  templateTaskId: string,
  body: Partial<ProjectTemplateTaskBody>,
): Promise<ProjectTemplateTask> {
  const response = await apiFetch(
    `${BASE}/config/templates/${templateId}/tasks/${templateTaskId}`,
    {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    },
  );
  if (!response.ok)
    throw new Error(await extractApiError(response, 'Failed to save the checklist item'));
  return response.json();
}

export async function deleteTemplateTask(
  templateId: string,
  templateTaskId: string,
): Promise<void> {
  const response = await apiFetch(
    `${BASE}/config/templates/${templateId}/tasks/${templateTaskId}`,
    { method: 'DELETE' },
  );
  if (!response.ok)
    throw new Error(await extractApiError(response, 'Failed to remove the checklist item'));
}

// ------------------------------------------------------------------- leads

const LEADS = `${BASE}/leads`;

export async function listLeads(
  params: LeadListParams = {},
): Promise<ListEnvelope<ProjectLead>> {
  const sp = new URLSearchParams();
  if (params.query) sp.set('query', params.query);
  (params.outcome ?? []).forEach((value) => sp.append('outcome', value));
  (params.status_id ?? []).forEach((value) => sp.append('status_id', value));
  (params.owner_user_id ?? []).forEach((value) => sp.append('owner_user_id', value));
  (params.customer_id ?? []).forEach((value) => sp.append('customer_id', value));
  (params.source ?? []).forEach((value) => sp.append('source', value));
  (params.acceptance_state ?? []).forEach((value) => sp.append('acceptance_state', value));
  if (params.page) sp.set('page', String(params.page));
  if (params.limit) sp.set('limit', String(params.limit));
  if (params.sort) sp.set('sort', params.sort);
  if (params.dir) sp.set('dir', params.dir);
  const query = sp.toString();
  const response = await apiFetch(`${LEADS}${query ? `?${query}` : ''}`);
  if (!response.ok) throw new Error(await extractApiError(response, 'Failed to load leads'));
  return response.json();
}

export async function getLead(leadId: string): Promise<ProjectLead> {
  const response = await apiFetch(`${LEADS}/${leadId}`);
  if (!response.ok) throw new Error(await extractApiError(response, 'Failed to load the lead'));
  return response.json();
}

export async function createLead(body: ProjectLeadBody): Promise<ProjectLead> {
  const response = await apiFetch(LEADS, { method: 'POST', body: JSON.stringify(body) });
  if (!response.ok) throw new Error(await extractApiError(response, 'Failed to record the lead'));
  return response.json();
}

export async function updateLead(
  leadId: string,
  body: Partial<ProjectLeadBody>,
): Promise<ProjectLead> {
  const response = await apiFetch(`${LEADS}/${leadId}`, {
    method: 'PUT',
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error(await extractApiError(response, 'Failed to save the lead'));
  return response.json();
}

export async function changeLeadStatus(
  leadId: string,
  toStatusId: string,
): Promise<ProjectLead> {
  const response = await apiFetch(`${LEADS}/${leadId}/status`, {
    method: 'POST',
    body: JSON.stringify({ to_status_id: toStatusId }),
  });
  if (!response.ok) throw new Error(await extractApiError(response, 'Failed to move the lead'));
  return response.json();
}

/**
 * What qualifying WOULD hit, before the user commits to it. Same matcher and
 * thresholds as registration, so the preview cannot disagree with the decision.
 */
export async function previewQualify(
  leadId: string,
  params: { title?: string | null; developer_party_id?: string | null } = {},
): Promise<ClashPreview> {
  const sp = new URLSearchParams();
  if (params.title) sp.set('title', params.title);
  if (params.developer_party_id) sp.set('developer_party_id', params.developer_party_id);
  const query = sp.toString();
  const response = await apiFetch(
    `${LEADS}/${leadId}/qualify-preview${query ? `?${query}` : ''}`,
  );
  if (!response.ok)
    throw new Error(await extractApiError(response, 'Failed to check for clashes'));
  return response.json();
}

/** Returns the PROJECT, because that is what qualifying produces (AC-O4). */
export async function qualifyLead(
  leadId: string,
  body: LeadQualifyBody = {},
): Promise<Project> {
  const response = await apiFetch(`${LEADS}/${leadId}/qualify`, {
    method: 'POST',
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error(await extractApiError(response, 'Failed to qualify the lead'));
  return response.json();
}

export async function disqualifyLead(
  leadId: string,
  reason: string,
): Promise<ProjectLead> {
  const response = await apiFetch(`${LEADS}/${leadId}/disqualify`, {
    method: 'POST',
    body: JSON.stringify({ reason }),
  });
  if (!response.ok)
    throw new Error(await extractApiError(response, 'Failed to disqualify the lead'));
  return response.json();
}

export async function reopenLead(leadId: string): Promise<ProjectLead> {
  const response = await apiFetch(`${LEADS}/${leadId}/reopen`, { method: 'POST' });
  if (!response.ok) throw new Error(await extractApiError(response, 'Failed to reopen the lead'));
  return response.json();
}

export async function deleteLead(leadId: string): Promise<void> {
  const response = await apiFetch(`${LEADS}/${leadId}`, { method: 'DELETE' });
  if (!response.ok) throw new Error(await extractApiError(response, 'Failed to delete the lead'));
}

export async function listDisqualifyReasons(): Promise<LeadReasonOption[]> {
  const response = await apiFetch(`${LEADS}/disqualify-reasons`);
  if (!response.ok) throw new Error(await extractApiError(response, 'Failed to load reasons'));
  return response.json();
}

export async function getLeadMetrics(): Promise<LeadConversionMetrics> {
  const response = await apiFetch(`${LEADS}/metrics`);
  if (!response.ok) throw new Error(await extractApiError(response, 'Failed to load lead metrics'));
  return response.json();
}

export async function getCustomerPortfolio(customerId: string): Promise<CustomerPortfolio> {
  const response = await apiFetch(`${LEADS}/by-customer/${customerId}/portfolio`);
  if (!response.ok)
    throw new Error(await extractApiError(response, 'Failed to load this account'));
  return response.json();
}

// -------------------------------------------------------------- quotations

/**
 * Lines hang off a VERSION, never off a quotation. That is the model, not a URL
 * accident: "which version was this line on" is the question the whole thing exists to
 * answer.
 */
export async function listQuotations(projectId: string): Promise<ProjectQuotation[]> {
  const response = await apiFetch(`${BASE}/projects/${projectId}/quotations`);
  if (!response.ok) throw new Error(await extractApiError(response, 'Failed to load quotations'));
  const body: ListEnvelope<ProjectQuotation> = await response.json();
  return body.data;
}

export async function createQuotation(
  projectId: string,
  body: ProjectQuotationBody,
): Promise<ProjectQuotation> {
  const response = await apiFetch(`${BASE}/projects/${projectId}/quotations`, {
    method: 'POST',
    body: JSON.stringify(body),
  });
  if (!response.ok)
    throw new Error(await extractApiError(response, 'Failed to create the quotation'));
  return response.json();
}

export async function updateQuotation(
  quotationId: string,
  body: Partial<ProjectQuotationBody>,
): Promise<ProjectQuotation> {
  const response = await apiFetch(`${BASE}/quotations/${quotationId}`, {
    method: 'PUT',
    body: JSON.stringify(body),
  });
  if (!response.ok)
    throw new Error(await extractApiError(response, 'Failed to save the quotation'));
  return response.json();
}

export async function deleteQuotation(quotationId: string): Promise<void> {
  const response = await apiFetch(`${BASE}/quotations/${quotationId}`, { method: 'DELETE' });
  if (!response.ok)
    throw new Error(await extractApiError(response, 'Failed to delete the quotation'));
}

export async function setQuotationOutcome(
  quotationId: string,
  body: QuotationOutcomeBody,
): Promise<ProjectQuotation> {
  const response = await apiFetch(`${BASE}/quotations/${quotationId}/outcome`, {
    method: 'POST',
    body: JSON.stringify(body),
  });
  if (!response.ok)
    throw new Error(await extractApiError(response, 'Failed to record the outcome'));
  return response.json();
}

export async function listQuotationVersions(
  quotationId: string,
): Promise<QuotationVersion[]> {
  const response = await apiFetch(`${BASE}/quotations/${quotationId}/versions`);
  if (!response.ok) throw new Error(await extractApiError(response, 'Failed to load versions'));
  const body: ListEnvelope<QuotationVersion> = await response.json();
  return body.data;
}

/** Freezes the current version and opens the next, carrying its lines (AC-E3). */
export async function reviseQuotation(quotationId: string): Promise<QuotationVersion> {
  const response = await apiFetch(`${BASE}/quotations/${quotationId}/revise`, {
    method: 'POST',
  });
  if (!response.ok)
    throw new Error(await extractApiError(response, 'Failed to open a new version'));
  return response.json();
}

export async function listQuotationLines(versionId: string): Promise<QuotationLine[]> {
  const response = await apiFetch(`${BASE}/quotation-versions/${versionId}/lines`);
  if (!response.ok) throw new Error(await extractApiError(response, 'Failed to load lines'));
  const body: ListEnvelope<QuotationLine> = await response.json();
  return body.data;
}

export async function createQuotationLine(
  versionId: string,
  body: QuotationLineBody,
): Promise<QuotationLine> {
  const response = await apiFetch(`${BASE}/quotation-versions/${versionId}/lines`, {
    method: 'POST',
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error(await extractApiError(response, 'Failed to add the line'));
  return response.json();
}

/**
 * The version's WHOLE line set, in one request (S10).
 *
 * Whole-set semantics, and the sharp edge is worth stating at the call site: **a stored line
 * whose id is missing from `lines` is DELETED**. Send everything on screen, never only what
 * changed. Position in the array is the order, so `sort_order` is not sent at all.
 *
 * A frozen or issued version is refused with the same `quotation_version_issued` 422 the
 * per-row routes raise, before anything is written.
 */
export async function replaceQuotationLines(
  versionId: string,
  lines: QuotationLineBulkItem[],
): Promise<QuotationLine[]> {
  const response = await apiFetch(`${BASE}/quotation-versions/${versionId}/lines`, {
    method: 'PUT',
    body: JSON.stringify({ lines }),
  });
  if (!response.ok) throw new Error(await extractApiError(response, 'Failed to save the lines'));
  const body: ListEnvelope<QuotationLine> = await response.json();
  return body.data;
}

export async function updateQuotationLine(
  versionId: string,
  lineId: string,
  body: Partial<QuotationLineBody>,
): Promise<QuotationLine> {
  const response = await apiFetch(`${BASE}/quotation-versions/${versionId}/lines/${lineId}`, {
    method: 'PUT',
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error(await extractApiError(response, 'Failed to save the line'));
  return response.json();
}

export async function deleteQuotationLine(
  versionId: string,
  lineId: string,
): Promise<void> {
  const response = await apiFetch(`${BASE}/quotation-versions/${versionId}/lines/${lineId}`, {
    method: 'DELETE',
  });
  if (!response.ok)
    throw new Error(await extractApiError(response, 'Failed to delete the line'));
}

export async function listQuotationLossReasons(): Promise<{ value: string; label: string }[]> {
  const response = await apiFetch(`${BASE}/config/loss-reasons`);
  if (!response.ok) throw new Error(await extractApiError(response, 'Failed to load reasons'));
  return response.json();
}

// ------------------------------------------------- series and price floors

export async function listSeries(includeInactive = false): Promise<ProjectSeries[]> {
  const response = await apiFetch(
    `${BASE}/config/series${includeInactive ? '?include_inactive=true' : ''}`,
  );
  if (!response.ok) throw new Error(await extractApiError(response, 'Failed to load series'));
  const body: ListEnvelope<ProjectSeries> = await response.json();
  return body.data;
}

export async function createSeries(body: ProjectSeriesBody): Promise<ProjectSeries> {
  const response = await apiFetch(`${BASE}/config/series`, {
    method: 'POST',
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error(await extractApiError(response, 'Failed to create the series'));
  return response.json();
}

export async function updateSeries(
  seriesId: string,
  body: Partial<ProjectSeriesBody>,
): Promise<ProjectSeries> {
  const response = await apiFetch(`${BASE}/config/series/${seriesId}`, {
    method: 'PUT',
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error(await extractApiError(response, 'Failed to save the series'));
  return response.json();
}

export async function deleteSeries(seriesId: string): Promise<void> {
  const response = await apiFetch(`${BASE}/config/series/${seriesId}`, { method: 'DELETE' });
  if (!response.ok) throw new Error(await extractApiError(response, 'Failed to delete the series'));
}

/**
 * Load a list of product CODES onto a series (S18).
 *
 * Contract: `POST /config/series/{id}/products` with `{ codes, mode }` answers a
 * `SeriesProductImportResult`. The response ALWAYS carries `unmatched_codes` - a code the
 * catalogue does not stock is the admin's data telling them something, and the screen has
 * to show it rather than a smaller success number.
 */
export async function importSeriesProducts(
  seriesId: string,
  body: SeriesProductImportBody,
): Promise<SeriesProductImportResult> {
  const response = await apiFetch(`${BASE}/config/series/${seriesId}/products`, {
    method: 'POST',
    body: JSON.stringify(body),
  });
  if (!response.ok)
    throw new Error(await extractApiError(response, 'Failed to load the product list'));
  return response.json();
}

/**
 * The same import off a spreadsheet, QUEUED. The server reads the PRODUCT CODE column by
 * heading across every sheet, so nothing here has to know where the client keeps it.
 *
 * Answers 202 with a job id, not a report: the client's workbook is 9.2 MB and opening it
 * inside the request stalled the whole API process. Poll `getImportJobStatus` for the
 * report, which arrives in the SAME shape the paste path returns.
 */
export async function uploadSeriesProducts(
  seriesId: string,
  file: File,
  mode: SeriesProductImportBody['mode'],
): Promise<SeriesProductImportJob> {
  const form = new FormData();
  form.append('file', file);
  form.append('mode', mode);
  const response = await apiFetch(`${BASE}/config/series/${seriesId}/products/upload`, {
    method: 'POST',
    body: form,
  });
  if (!response.ok)
    throw new Error(await extractApiError(response, 'Failed to read that file'));
  return response.json();
}

/**
 * One poll of a queued import.
 *
 * Shared job infrastructure, not a series-specific route: `import_jobs` already reports
 * status, progress, the finished `result` and the error, and adding a second status
 * endpoint would be a second thing to keep in step with the first.
 */
export async function getImportJobStatus(jobId: string): Promise<ImportJobStatus> {
  const response = await apiFetch(`/api/system/jobs/${jobId}/status`);
  if (!response.ok)
    throw new Error(await extractApiError(response, 'Could not check the import'));
  return response.json();
}

/**
 * Judge one DRAFT line now, before any save.
 *
 * The verdicts cannot be computed here: series membership counts nominated CATEGORIES the
 * browser never fetched, and the floor walks the category ancestry. The server answers
 * with the same functions the save runs, so the live badge and the saved one cannot
 * disagree. Nothing is written.
 */
export async function judgeQuotationLine(
  quotationId: string,
  params: { product_id?: string; unit_price?: string },
): Promise<QuotationLineVerdict> {
  const search = new URLSearchParams();
  if (params.product_id) search.set('product_id', params.product_id);
  if (params.unit_price) search.set('unit_price', params.unit_price);
  const qs = search.toString();
  const response = await apiFetch(
    `${BASE}/quotations/${quotationId}/line-verdict${qs ? `?${qs}` : ''}`,
  );
  if (!response.ok)
    throw new Error(await extractApiError(response, 'Could not check the line'));
  return response.json();
}

/**
 * The photo and drawing cells a line with this product would show, before the line is saved.
 *
 * Contract: `GET /quotation-line-images?product_id=` answers `{product_image, technical_drawing}`
 * in the shapes of a saved editable line with no picks, candidates included.
 */
export async function getQuotationLineImages(productId: string): Promise<QuotationLineImages> {
  const search = new URLSearchParams({ product_id: productId });
  const response = await apiFetch(`${BASE}/quotation-line-images?${search.toString()}`);
  if (!response.ok)
    throw new Error(await extractApiError(response, 'Could not load the product pictures'));
  return response.json();
}

/**
 * Re-ask both guardrails for one version against today's master data (S19).
 *
 * Contract: `POST /quotation-versions/{id}/recompute` answers a `QuotationRecomputeResult`.
 * A frozen or issued version is refused with 422 - its flags are what was true when the
 * customer was sent the paper.
 */
export async function recomputeQuotationVersion(
  versionId: string,
): Promise<QuotationRecomputeResult> {
  const response = await apiFetch(`${BASE}/quotation-versions/${versionId}/recompute`, {
    method: 'POST',
  });
  if (!response.ok)
    throw new Error(await extractApiError(response, 'Failed to recompute this version'));
  return response.json();
}

export async function listPriceFloors(): Promise<PriceFloorRule[]> {
  const response = await apiFetch(`${BASE}/config/price-floors`);
  if (!response.ok)
    throw new Error(await extractApiError(response, 'Failed to load price floors'));
  const body: ListEnvelope<PriceFloorRule> = await response.json();
  return body.data;
}

/**
 * The floor in force for ONE product or ONE category, plus the rule set on it (if any).
 *
 * Contract:
 *   GET /api/v1/project-sales/config/price-floors/effective?product_id=<id>
 *   GET /api/v1/project-sales/config/price-floors/effective?category_id=<id>
 *   200 -> EffectivePriceFloor
 *   404 -> no such product / category
 *   422 -> neither target named, or both
 *   403 -> the caller lacks projects.types.view
 *
 * Resolution stays on the server. Working out which rule governs a product needs the
 * category ancestry walk, and a second implementation of that in the browser would be
 * free to disagree with what a quotation line is actually judged against.
 */
export async function getEffectivePriceFloor(target: {
  level: FloorTargetLevel;
  id: string;
}): Promise<EffectivePriceFloor> {
  const key = target.level === 'product' ? 'product_id' : 'category_id';
  const response = await apiFetch(
    `${BASE}/config/price-floors/effective?${key}=${encodeURIComponent(target.id)}`,
  );
  if (!response.ok)
    throw new Error(await extractApiError(response, 'Failed to load the price floor'));
  return response.json();
}

/** Upsert per level: one rule per target, so editing "the Basins floor" means that. */
export async function upsertPriceFloor(body: PriceFloorRuleBody): Promise<PriceFloorRule> {
  const response = await apiFetch(`${BASE}/config/price-floors`, {
    method: 'POST',
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error(await extractApiError(response, 'Failed to save the floor'));
  return response.json();
}

export async function deletePriceFloor(ruleId: string): Promise<void> {
  const response = await apiFetch(`${BASE}/config/price-floors/${ruleId}`, {
    method: 'DELETE',
  });
  if (!response.ok) throw new Error(await extractApiError(response, 'Failed to delete the floor'));
}

// ------------------------------------------------------- S4 samples and POs

export async function listSamples(
  projectId: string,
  versionId?: string,
): Promise<ProjectSample[]> {
  const query = versionId ? `?version_id=${encodeURIComponent(versionId)}` : '';
  const response = await apiFetch(`${BASE}/projects/${projectId}/samples${query}`);
  if (!response.ok) throw new Error(await extractApiError(response, 'Failed to load samples'));
  const body: ListEnvelope<ProjectSample> = await response.json();
  return body.data;
}

export async function createSample(
  projectId: string,
  body: ProjectSampleBody,
): Promise<ProjectSample> {
  const response = await apiFetch(`${BASE}/projects/${projectId}/samples`, {
    method: 'POST',
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error(await extractApiError(response, 'Failed to record the sample'));
  return response.json();
}

export async function updateSample(
  sampleId: string,
  body: Partial<ProjectSampleBody>,
): Promise<ProjectSample> {
  const response = await apiFetch(`${BASE}/samples/${sampleId}`, {
    method: 'PUT',
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error(await extractApiError(response, 'Failed to save the sample'));
  return response.json();
}

export async function deleteSample(sampleId: string): Promise<void> {
  const response = await apiFetch(`${BASE}/samples/${sampleId}`, { method: 'DELETE' });
  if (!response.ok) throw new Error(await extractApiError(response, 'Failed to delete the sample'));
}

export async function listPurchaseOrders(
  projectId: string,
): Promise<ProjectPurchaseOrder[]> {
  const response = await apiFetch(`${BASE}/projects/${projectId}/purchase-orders`);
  if (!response.ok)
    throw new Error(await extractApiError(response, 'Failed to load purchase orders'));
  const body: ListEnvelope<ProjectPurchaseOrder> = await response.json();
  return body.data;
}


export async function createPurchaseOrder(
  projectId: string,
  body: ProjectPurchaseOrderBody,
): Promise<ProjectPurchaseOrder> {
  const response = await apiFetch(`${BASE}/projects/${projectId}/purchase-orders`, {
    method: 'POST',
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error(await extractApiError(response, 'Failed to record the PO'));
  return response.json();
}

/**
 * The PO's header and, when `body.lines` is set, its WHOLE line set in one request.
 *
 * One request rather than a header PUT followed by a line write: two could half-land, leaving
 * a renamed PO whose lines never moved, and the edit view promises that a Save is the
 * arrangement the user made or nothing at all.
 *
 * The sharp edge, at the call site: **a stored line whose id is missing from `lines` is
 * DELETED**. Send everything on screen, never only what changed. Position in the array is the
 * order, so `sort_order` is not sent. Omit `lines` entirely for a header-only save.
 */
export async function updatePurchaseOrder(
  poId: string,
  body: ProjectPurchaseOrderSaveBody,
): Promise<ProjectPurchaseOrder> {
  const response = await apiFetch(`${BASE}/purchase-orders/${poId}`, {
    method: 'PUT',
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error(await extractApiError(response, 'Failed to save the PO'));
  return response.json();
}

export async function deletePurchaseOrder(poId: string): Promise<void> {
  const response = await apiFetch(`${BASE}/purchase-orders/${poId}`, { method: 'DELETE' });
  if (!response.ok) throw new Error(await extractApiError(response, 'Failed to delete the PO'));
}

export async function listPurchaseOrderLines(poId: string): Promise<PurchaseOrderLine[]> {
  const response = await apiFetch(`${BASE}/purchase-orders/${poId}/lines`);
  if (!response.ok) throw new Error(await extractApiError(response, 'Failed to load PO lines'));
  const body: ListEnvelope<PurchaseOrderLine> = await response.json();
  return body.data;
}

export async function createPurchaseOrderLine(
  poId: string,
  body: PurchaseOrderLineBody,
): Promise<PurchaseOrderLine> {
  const response = await apiFetch(`${BASE}/purchase-orders/${poId}/lines`, {
    method: 'POST',
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error(await extractApiError(response, 'Failed to add the line'));
  return response.json();
}

export async function updatePurchaseOrderLine(
  poId: string,
  lineId: string,
  body: Partial<PurchaseOrderLineBody>,
): Promise<PurchaseOrderLine> {
  const response = await apiFetch(`${BASE}/purchase-orders/${poId}/lines/${lineId}`, {
    method: 'PUT',
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error(await extractApiError(response, 'Failed to save the line'));
  return response.json();
}

export async function deletePurchaseOrderLine(poId: string, lineId: string): Promise<void> {
  const response = await apiFetch(`${BASE}/purchase-orders/${poId}/lines/${lineId}`, {
    method: 'DELETE',
  });
  if (!response.ok) throw new Error(await extractApiError(response, 'Failed to remove the line'));
}

export async function listProjectSponsorships(
  projectId: string,
): Promise<ProjectSponsorship[]> {
  const response = await apiFetch(`${BASE}/projects/${projectId}/sponsorships`);
  if (!response.ok)
    throw new Error(await extractApiError(response, 'Failed to load sponsorships'));
  const body: ListEnvelope<ProjectSponsorship> = await response.json();
  return body.data;
}

export async function getSponsorshipRollup(projectId: string): Promise<SponsorshipRollup> {
  const response = await apiFetch(`${BASE}/projects/${projectId}/sponsorships/rollup`);
  if (!response.ok)
    throw new Error(await extractApiError(response, 'Failed to load the sponsorship rollup'));
  return response.json();
}

export async function getProjectDashboard(): Promise<ProjectDashboard> {
  const response = await apiFetch(`${BASE}/reports/dashboard`);
  if (!response.ok)
    throw new Error(await extractApiError(response, 'Failed to load the dashboard'));
  return response.json();
}

/**
 * The products on a series with what they sell for (T2).
 *
 * `/products/rows` rather than a GET on `/products`: that path already means "load a list of
 * codes onto this series", and giving one path two meanings by method is how a route ends up
 * doing a job nobody can name.
 *
 * `derived_floor` arrives computed. It is NOT recalculated in the browser - it is the number
 * a refusal is argued from, and the pricing engine enforces with the same server-side
 * function, so a second implementation here could disagree with the one that blocks a save.
 */
export async function getSeriesProductRows(
  seriesId: string,
): Promise<SeriesProductRow[]> {
  const response = await apiFetch(`${BASE}/config/series/${seriesId}/products/rows`);
  if (!response.ok)
    throw new Error(await extractApiError(response, 'Failed to load the series products'));
  const body = await response.json();
  return body.data ?? [];
}

/**
 * Set or clear one product's price and percentage.
 *
 * `null` CLEARS, because the person emptied the cell. That is the opposite of the sheet
 * importer, where an absent value means the sheet did not say and the stored number stands.
 */
export async function updateSeriesProductPricing(
  seriesId: string,
  productId: string,
  body: { selling_price: string | null; max_discount_pct: string | null },
): Promise<SeriesProductRow> {
  const response = await apiFetch(
    `${BASE}/config/series/${seriesId}/products/${productId}`,
    { method: 'PATCH', body: JSON.stringify(body) },
  );
  if (!response.ok) throw new Error(await extractApiError(response, 'Failed to save the price'));
  return response.json();
}

export async function removeSeriesProduct(
  seriesId: string,
  productId: string,
): Promise<void> {
  const response = await apiFetch(
    `${BASE}/config/series/${seriesId}/products/${productId}`,
    { method: 'DELETE' },
  );
  if (!response.ok)
    throw new Error(await extractApiError(response, 'Failed to remove the product'));
}
