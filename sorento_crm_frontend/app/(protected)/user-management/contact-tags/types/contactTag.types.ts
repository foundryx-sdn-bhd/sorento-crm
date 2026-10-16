export interface ContactTag {
  id: string;
  name: string;
  description: string | null;
  is_active: boolean;
  sort_order: number;
  settings: Record<string, unknown>;
  contact_count: number;
  /** Null when counts were not asked for, or there are too many holders to evaluate. */
  differs_count?: number | null;
  created_at: string;
  updated_at: string;
}

export interface ContactTagListResponse {
  data: ContactTag[];
  pagination?: { total: number; page?: number; limit?: number; pages?: number };
}

export interface ContactTagInput {
  name: string;
  description?: string | null;
  is_active?: boolean;
  sort_order?: number;
  settings?: Record<string, unknown>;
}

export type BulkTagStatus = 'changed' | 'unchanged' | 'failed';

export interface BulkTagRow {
  contact_id: string;
  label: string;
  status: BulkTagStatus;
  tags_before: string[];
  tags_after: string[];
  error: string | null;
  /** S3: the setting changes the add would apply (same shape as an apply row). */
  settings?: ApplyRow | null;
}

export interface BulkTagResult {
  dry_run: boolean;
  results: BulkTagRow[];
  counts: { changed: number; unchanged: number; failed: number };
}

export interface BulkTagInput {
  contactIds: string[];
  addTagIds: string[];
  removeTagIds: string[];
  dryRun: boolean;
  /** Add mode only: apply the added tags' settings after the tag change. */
  applySettings?: boolean;
}

export interface CatalogOption {
  value: string;
  label: string;
  code?: string;
}

export interface SettingsCatalog {
  access_types: CatalogOption[];
  brands: CatalogOption[];
  market_segments: CatalogOption[];
  attachment_types: CatalogOption[];
  /** Absent when the viewer may not read them (stock / agents / companies). */
  warehouses?: CatalogOption[];
  agents?: CatalogOption[];
  agent_fields?: { agent_code: string; resource: string; field_key: string }[];
  companies?: CatalogOption[];
  field_reveals: CatalogOption[];
  portal_forms: CatalogOption[];
  spec_keys: CatalogOption[];
}

export type TagSettingsValue = Record<string, unknown>;

export interface ApplyChange {
  section: string;
  key: string;
  label: string;
  before: unknown;
  after: unknown;
  decided_by: string[];
  added: string[];
  removed: string[];
}

export interface ApplyRow {
  contact_id?: string;
  label?: string | null;
  status: 'changed' | 'unchanged' | 'skipped' | 'failed';
  changes: ApplyChange[];
  conflicts: { label: string; tags: { name: string; value: unknown }[] }[];
  not_applied: { label: string; reason: string }[];
  error?: string | null;
}

export interface ApplyResult {
  dry_run: boolean;
  results: (ApplyRow & { contact_id: string })[];
  counts: { changed: number; unchanged: number; skipped: number; failed: number; conflicts: number };
}
