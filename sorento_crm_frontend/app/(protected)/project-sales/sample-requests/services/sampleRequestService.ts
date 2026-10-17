import { apiFetch } from '@/lib/api';
import { buildDataGridParams, extractApiError } from '@/lib/api-client';
import { saveBlobAs } from '@/lib/save-blob';
import type { DataGridApiFetchParams } from '@/components/ui/data-grid';
import {
  SampleRequestError,
  type SampleRequestDetail,
  type SampleRequestPage,
  type SampleRequestProductHit,
  type SampleRequestStatus,
  type SampleRequestWrite,
} from '@/types/sampleRequest';

const BASE = '/api/v1/project-sales';

export type SampleRequestListParams = DataGridApiFetchParams & {
  /** Comma list of status keys; empty means the server default (open requests). */
  status?: string;
  projectId?: string;
  salespersonId?: string;
};

/** A refusal that names a field arrives as `{message, code, details: {field}}`. */
async function fail(res: Response, fallback: string): Promise<never> {
  const spare = res.clone();
  const message = await extractApiError(res, fallback);
  type Body = { code?: unknown; details?: { field?: unknown } };
  let body: Body | null = null;
  try {
    body = (await spare.json()) as Body;
  } catch {
    body = null;
  }
  throw new SampleRequestError(
    message,
    typeof body?.code === 'string' ? body.code : null,
    typeof body?.details?.field === 'string' ? body.details.field : null,
  );
}

async function read<T>(res: Response, fallback: string): Promise<T> {
  if (!res.ok) return fail(res, fallback);
  return (await res.json()) as T;
}

export async function getSampleRequests(params: SampleRequestListParams): Promise<SampleRequestPage> {
  const sp = buildDataGridParams(
    { ...params, searchQuery: undefined },
    {
      q: params.searchQuery,
      status: params.status,
      project_id: params.projectId,
      salesperson_id: params.salespersonId,
    },
  );
  const res = await apiFetch(`${BASE}/sample-requests?${sp.toString()}`);
  return read<SampleRequestPage>(res, 'Failed to load sample requests');
}

/** The project tab: every status of that project's requests, one page the grid pages itself. */
export async function getProjectSampleRequests(projectId: string): Promise<SampleRequestPage> {
  const res = await apiFetch(
    `${BASE}/projects/${encodeURIComponent(projectId)}/sample-requests?limit=200`,
  );
  return read<SampleRequestPage>(res, 'Failed to load sample requests');
}

export async function getSampleRequest(id: string): Promise<SampleRequestDetail> {
  const res = await apiFetch(`${BASE}/sample-requests/${encodeURIComponent(id)}`);
  return read<SampleRequestDetail>(res, 'Failed to load the sample request');
}

export async function createSampleRequest(data: SampleRequestWrite): Promise<SampleRequestDetail> {
  const res = await apiFetch(`${BASE}/sample-requests`, {
    method: 'POST',
    body: JSON.stringify(data),
  });
  return read<SampleRequestDetail>(res, 'Failed to save the sample request');
}

export async function updateSampleRequest(
  id: string,
  data: SampleRequestWrite,
): Promise<SampleRequestDetail> {
  const res = await apiFetch(`${BASE}/sample-requests/${encodeURIComponent(id)}`, {
    method: 'PUT',
    body: JSON.stringify(data),
  });
  return read<SampleRequestDetail>(res, 'Failed to save the sample request');
}

export async function submitSampleRequest(id: string): Promise<SampleRequestDetail> {
  const res = await apiFetch(`${BASE}/sample-requests/${encodeURIComponent(id)}/submit`, {
    method: 'POST',
  });
  return read<SampleRequestDetail>(res, 'Failed to submit the sample request');
}

export interface SampleRequestMoveBody {
  to: SampleRequestStatus;
  date?: string;
  reason?: string;
}

export async function moveSampleRequest(
  id: string,
  body: SampleRequestMoveBody,
): Promise<SampleRequestDetail> {
  const res = await apiFetch(`${BASE}/sample-requests/${encodeURIComponent(id)}/move`, {
    method: 'POST',
    body: JSON.stringify(body),
  });
  return read<SampleRequestDetail>(res, 'Failed to move the sample request');
}

export async function saveSampleRequestInternal(
  id: string,
  body: { consignment_no: string | null; po_no: string | null },
): Promise<SampleRequestDetail> {
  const res = await apiFetch(`${BASE}/sample-requests/${encodeURIComponent(id)}/internal`, {
    method: 'PUT',
    body: JSON.stringify(body),
  });
  return read<SampleRequestDetail>(res, 'Failed to save the consignment details');
}

export async function downloadSampleRequestPdf(id: string, requestNo: string): Promise<void> {
  const res = await apiFetch(`${BASE}/sample-requests/${encodeURIComponent(id)}/pdf`);
  if (!res.ok) throw new Error(await extractApiError(res, 'Failed to download the PDF'));
  saveBlobAs(await res.blob(), `${requestNo}.pdf`);
}

export async function searchSampleRequestProducts(q: string): Promise<SampleRequestProductHit[]> {
  const res = await apiFetch(`${BASE}/sample-requests/products?q=${encodeURIComponent(q)}`);
  return read<SampleRequestProductHit[]>(res, 'Failed to search products');
}

/** Whether the System Settings switch is on; read from the open app-config projection. */
export async function getSampleRequestSwitches(): Promise<{
  requireProject: boolean;
}> {
  const res = await apiFetch('/api/user-management/settings/app-config');
  const body = await read<{
    sample_request_require_project?: boolean;
  }>(res, 'Failed to load settings');
  return {
    requireProject: Boolean(body.sample_request_require_project),
  };
}
