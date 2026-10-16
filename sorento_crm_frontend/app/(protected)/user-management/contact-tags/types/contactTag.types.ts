export interface ContactTag {
  id: string;
  name: string;
  description: string | null;
  is_active: boolean;
  sort_order: number;
  settings: Record<string, unknown>;
  contact_count: number;
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
}

export type BulkTagStatus = 'changed' | 'unchanged' | 'failed';

export interface BulkTagRow {
  contact_id: string;
  label: string;
  status: BulkTagStatus;
  tags_before: string[];
  tags_after: string[];
  error: string | null;
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
}
