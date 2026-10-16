/**
 * Certificate register - feature service.
 *
 * Layering: components -> hooks/useCertificates -> THIS service -> lib/api-client
 * -> backend. No component fetches directly.
 *
 * Backend contract as SHIPPED (mount: `master_data`, permission slugs
 * `master_data.certificates.{view,add,edit,delete}`):
 *
 *   GET    /api/v1/master-data/certificates/
 *          Note the trailing slash: the collection route is mounted with one, and
 *          omitting it costs a 307 on every list load.
 *          Query: page, limit, sort, dir, query (number search, punctuation- and
 *          whitespace-insensitive server-side), certificate_ids, certificate_number,
 *          product_ids, scheme, status, validity_state (COMMA-SEPARATED list, e.g.
 *          `expiring_soon,expired` - what the validity-scoped default view sends),
 *          expiring_within_days, valid_on, needs_review, expiry_notify_batch_id,
 *          resolve_signed_urls, contact_id, space_id.
 *          200: { data: Certificate[], empty, pagination: { total, page, limit } }
 *          List rows omit `revisions` / `products` / `reminders`; every row carries
 *          the DERIVED `validity_state` / `is_expired` / `days_until_expiry` and
 *          `covered_product_count`.
 *
 *   GET    /api/v1/master-data/certificates/{id}
 *          200: Certificate WITH `revisions` (newest first), `products`,
 *          `unmatched_products`, `reminders`, `current_revision` and
 *          `possible_duplicate_of` resolved to { id, scheme, certificate_number }
 *          so the UI never renders an id. 404 cross-company (SEC-2).
 *          Revision rows carry `attachment_filename` / `attachment_is_deleted`;
 *          signed `preview_url` / `download_url` are present for the CURRENT
 *          revision only, so superseded rows render without controls (MCP-8).
 *
 *   POST   /api/v1/master-data/certificates/          body: CertificateFormData
 *          201: Certificate. **400** (not 409) when the normalized identity
 *          (scheme + number) already exists - the route raises a validation error.
 *
 *   PUT    /api/v1/master-data/certificates/{id}      body: Partial<CertificateFormData>
 *          200: Certificate. `issued_at` / `valid_from` / `valid_until` apply to the
 *          CURRENT revision; only fields actually sent are written, so a partial
 *          edit never nulls a date. Fixing a flagged field clears needs_review (RVW-4).
 *
 *   DELETE /api/v1/master-data/certificates/{id}      **200** with a message body.
 *          Hard delete; cascades revisions, coverage and projection rows, and
 *          leaves the `attachments` rows alone (COV-5).
 *
 *   DELETE /api/v1/master-data/certificates/bulk      body: { ids: string[] }
 *          200: { message, deleted_count }
 *
 *   POST   /api/v1/master-data/certificates/{id}/merge-into/{target_id}
 *          200: the TARGET certificate. Moves revisions onto the target as
 *          `revision_no = target max + 1 ...`, re-points the projection, unions
 *          coverage (source preserved) and hard-deletes the emptied source.
 *          422 on cross-company or `target_id == id` (DUP-4 / DUP-5).
 *
 *   POST   /api/v1/master-data/certificates/{id}/products   body: { product_id }
 *          Coverage row written with `source='manual'`, plus the projection row
 *          for the current revision's attachment (COV-3).
 *
 *   DELETE /api/v1/master-data/certificates/{id}/products/{coverage_id}
 *          **200**. Hard-deletes the coverage row AND its projection row.
 */
import { apiFetch } from '@/lib/api';
import { buildDataGridParams, extractApiError } from '@/lib/api-client';
import type { DataGridApiFetchParams, DataGridApiResponse } from '@/components/ui/data-grid';
import type { Certificate, CertificateFormData } from '../types/certificate.types';
import { codeWithName } from '@/lib/product-display';

const BASE = '/api/v1/master-data/certificates';

export type CertificatesListParams = DataGridApiFetchParams & {
  /** Comma-separated list; the default view sends `expiring_soon,expired`. */
  validity_state?: string;
  expiring_within_days?: number;
  scheme?: string;
  status?: string;
  needs_review?: boolean;
  expiry_notify_batch_id?: string;
};

export async function getCertificates(
  params: CertificatesListParams,
): Promise<DataGridApiResponse<Certificate>> {
  const qs = buildDataGridParams(params, {
    validity_state: params.validity_state,
    expiring_within_days: params.expiring_within_days,
    scheme: params.scheme,
    status: params.status,
    needs_review: params.needs_review,
    expiry_notify_batch_id: params.expiry_notify_batch_id,
  });
  // Trailing slash: the collection route is mounted at `/certificates/`, so
  // omitting it costs a 307 redirect on every list load.
  const response = await apiFetch(`${BASE}/?${qs.toString()}`);
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to fetch certificates'));
  }
  return response.json();
}

export async function getCertificate(id: string): Promise<Certificate> {
  const response = await apiFetch(`${BASE}/${id}`);
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to fetch certificate'));
  }
  return response.json();
}

export async function createCertificate(data: CertificateFormData): Promise<Certificate> {
  const response = await apiFetch(BASE, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to create certificate'));
  }
  return response.json();
}

export async function updateCertificate(
  id: string,
  data: Partial<CertificateFormData>,
): Promise<Certificate> {
  const response = await apiFetch(`${BASE}/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to update certificate'));
  }
  return response.json();
}

export async function bulkDeleteCertificates(
  ids: string[],
): Promise<{ message: string; deleted_count: number }> {
  const response = await apiFetch(`${BASE}/bulk`, {
    method: 'DELETE',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ids }),
  });
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to delete certificates'));
  }
  return response.json();
}

export async function mergeCertificateInto(id: string, targetId: string): Promise<Certificate> {
  const response = await apiFetch(`${BASE}/${id}/merge-into/${targetId}`, { method: 'POST' });
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to merge certificate'));
  }
  return response.json();
}

export async function addCertificateProduct(
  id: string,
  productId: string,
): Promise<Certificate> {
  const response = await apiFetch(`${BASE}/${id}/products`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ product_id: productId }),
  });
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to add covered product'));
  }
  return response.json();
}

export async function removeCertificateProduct(id: string, coverageId: string): Promise<void> {
  const response = await apiFetch(`${BASE}/${id}/products/${coverageId}`, { method: 'DELETE' });
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to remove covered product'));
  }
}

/**
 * Product options for the coverage picker.
 *
 * Uses the shared products select endpoint rather than a certificate-specific
 * route: coverage is a link to an existing product, so there is nothing
 * certificate-shaped about the option list.
 */
export async function getCertificateProductOptions(): Promise<
  { value: string; label: string }[]
> {
  const response = await apiFetch('/api/v1/master-data/products/select');
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to load products'));
  }
  const body = (await response.json()) as {
    data: { id: string; product_code: string; product_name: string }[];
  };
  return (body.data ?? []).map((p) => ({
    value: p.id,
    label: codeWithName(p.product_code, p.product_name),
  }));
}
