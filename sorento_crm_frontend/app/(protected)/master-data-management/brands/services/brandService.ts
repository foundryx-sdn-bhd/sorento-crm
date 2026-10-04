import { apiFetch } from '@/lib/api';
import type { Brand, BrandFormData } from '../types/brand.types';
import type { DataGridApiFetchParams, DataGridApiResponse } from '@/components/ui/data-grid';

export async function getBrands(
  params: DataGridApiFetchParams & { companyScope?: 'grants' },
): Promise<DataGridApiResponse<Brand>> {
  const { pageIndex, pageSize, sorting, searchQuery, companyScope } = params;
  const sortField = sorting?.[0]?.id || '';
  const sortDirection = sorting?.[0]?.desc ? 'desc' : 'asc';

  const queryParams = new URLSearchParams({
    page: String(pageIndex + 1),
    limit: String(pageSize),
    ...(sortField ? { sort: sortField, dir: sortDirection } : {}),
    ...(searchQuery ? { query: searchQuery } : {}),
    ...(companyScope ? { company_scope: companyScope } : {}),
  });

  const response = await apiFetch(`/api/v1/master-data/brands?${queryParams.toString()}`);

  if (!response.ok) {
    throw new Error('Failed to fetch brands');
  }

  return response.json();
}

export async function getBrand(id: string): Promise<Brand> {
  const response = await apiFetch(`/api/v1/master-data/brands/${id}`);
  if (!response.ok) throw new Error('Failed to fetch brand');
  return response.json();
}

export async function createBrand(data: BrandFormData): Promise<Brand> {
  const response = await apiFetch('/api/v1/master-data/brands', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ message: 'Failed to create brand' }));
    throw new Error(error.message);
  }
  return response.json();
}

export async function updateBrand(id: string, data: Partial<BrandFormData>): Promise<Brand> {
  const response = await apiFetch(`/api/v1/master-data/brands/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ message: 'Failed to update brand' }));
    throw new Error(error.message);
  }
  return response.json();
}

export async function deleteBrand(id: string): Promise<void> {
  const response = await apiFetch(`/api/v1/master-data/brands/${id}`, { method: 'DELETE' });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ message: 'Failed to delete brand' }));
    throw new Error(error.message);
  }
}
