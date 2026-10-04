/**
 * ============================================================================
 * Warehouses - feature service
 * ============================================================================
 * Layering: components -> hooks (useWarehouses*) -> THIS service -> lib/api -> backend.
 *
 * -- BACKEND CONTRACT for `fulfilment_planning` (borrow ladder v7.1 S1, migration 443) --
 *
 *   warehouses.fulfilment_planning : boolean, NOT NULL, DEFAULT false
 *     Seeded true for every ACTIVE bin whose `warehouse_code` ends in `-BB`, `-IB`,
 *     `-IR`, `-NTC` or `-AM` (24 of 83 on the 29 Aug dev copy); false everywhere else,
 *     pools included. A bin that is off is outside fulfilment planning entirely
 *     (UAC R17 / AC-S1-1).
 *
 *   GET  /api/v1/inventory/warehouses          -> each row carries `fulfilment_planning`;
 *                                              `sort=fulfilment_planning` is a valid sort key
 *                                              (the list column header offers it)
 *   GET  /api/v1/inventory/warehouses/{id}     -> carries `fulfilment_planning`
 *   POST /api/v1/inventory/warehouses          body may carry `fulfilment_planning`
 *   PUT  /api/v1/inventory/warehouses/{id}     body may carry `fulfilment_planning`
 * ============================================================================
 */
import { apiFetch } from '@/lib/api';
import type { Warehouse, WarehouseFormData } from '../types/warehouse.types';
import type { DataGridApiFetchParams, DataGridApiResponse } from '@/components/ui/data-grid';

export async function getWarehouses(
  params: DataGridApiFetchParams & { is_active?: boolean; companyScope?: 'grants' },
): Promise<DataGridApiResponse<Warehouse>> {
  const { pageIndex, pageSize, sorting, searchQuery, is_active, companyScope } = params;
  const sortField = sorting?.[0]?.id || '';
  const sortDirection = sorting?.[0]?.desc ? 'desc' : 'asc';
  const queryParams = new URLSearchParams({
    page: String(pageIndex + 1),
    limit: String(pageSize),
    ...(sortField ? { sort: sortField, dir: sortDirection } : {}),
    ...(searchQuery ? { query: searchQuery } : {}),
    ...(is_active !== undefined ? { is_active: String(is_active) } : {}),
    ...(companyScope ? { company_scope: companyScope } : {}),
  });
  const response = await apiFetch(`/api/v1/inventory/warehouses?${queryParams.toString()}`);
  if (!response.ok) throw new Error('Failed to fetch warehouses');
  return response.json();
}

export async function getWarehouse(id: string): Promise<Warehouse> {
  const response = await apiFetch(`/api/v1/inventory/warehouses/${id}`);
  if (!response.ok) throw new Error('Failed to fetch warehouse');
  return response.json();
}

export async function createWarehouse(data: WarehouseFormData): Promise<Warehouse> {
  const response = await apiFetch('/api/v1/inventory/warehouses', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ message: 'Failed to create warehouse' }));
    throw new Error(error.message);
  }
  return response.json();
}

export async function updateWarehouse(id: string, data: Partial<WarehouseFormData>): Promise<Warehouse> {
  const response = await apiFetch(`/api/v1/inventory/warehouses/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ message: 'Failed to update warehouse' }));
    throw new Error(error.message);
  }
  return response.json();
}

export async function deleteWarehouse(id: string): Promise<void> {
  const response = await apiFetch(`/api/v1/inventory/warehouses/${id}`, { method: 'DELETE' });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ message: 'Failed to delete warehouse' }));
    throw new Error(error.message);
  }
}

export interface BulkDeleteWarehousesResult {
  deleted_count: number;
  failed: Array<{ id: string; reason: string }>;
  message: string;
}

export async function bulkDeleteWarehouses(ids: string[]): Promise<BulkDeleteWarehousesResult> {
  const response = await apiFetch('/api/v1/inventory/warehouses/bulk', {
    method: 'DELETE',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ids }),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ message: 'Failed to bulk delete warehouses' }));
    throw new Error(error.message || 'Failed to bulk delete warehouses');
  }
  return response.json();
}

export interface ValidateWarehouseImportResult {
  valid: boolean;
  errors: string[];
  warnings: Array<string | { row?: number; message?: string }>;
  summary?: Record<string, unknown>;
}

export async function validateWarehouseImport(
  data: Record<string, unknown>[],
): Promise<ValidateWarehouseImportResult> {
  const response = await apiFetch('/api/v1/inventory/warehouses/bulk-import', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ warehouses: data, validate_only: true }),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Validation failed' }));
    const message =
      typeof error.detail === 'string'
        ? error.detail
        : Array.isArray(error.detail)
          ? error.detail.map((e: { msg?: string }) => e.msg || String(e)).join('; ')
          : error.message || 'Validation failed';
    throw new Error(message);
  }
  return response.json();
}

export async function bulkImportWarehouses(
  data: Record<string, unknown>[],
): Promise<{ job_id: string; status: string; message: string }> {
  const response = await apiFetch('/api/v1/inventory/warehouses/bulk-import', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ warehouses: data }),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ message: 'Failed to queue import job' }));
    throw new Error(error.message || 'Failed to queue import job');
  }
  return response.json();
}
