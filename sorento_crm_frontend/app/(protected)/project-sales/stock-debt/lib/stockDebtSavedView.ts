/**
 * STOCK-DEBT-SAVED-VIEWS: the pure mapping between the board's filters and a saved view's
 * `quick_filters`. The delivery date range is stored as whole days from TODAY (local date)
 * and recomputed on apply, so a saved "next 30 days" stays "next 30 days".
 */
import { parseIsoDate, toIsoDate } from '@/components/ui/date-range-picker';
import type { SavedView, SavedViewConfig } from '@/services/savedViewsService';
import type { StockDebtLocationsMode } from '../services/stockDebtService';
import type { StockDebtBook } from '../types/stockDebt.types';

export const STOCK_DEBT_LISTING_KEY = 'projects.stock_debt.view::stock-debt';

export interface StockDebtFilters {
  query: string;
  book: StockDebtBook;
  supplierIds: string[];
  locations: string[];
  locationsMode: StockDebtLocationsMode;
  /** `YYYY-MM-DD`, '' = open. */
  dateFrom: string;
  dateTo: string;
  onlyDebt: boolean;
  projected: boolean;
}

export const DEFAULT_STOCK_DEBT_FILTERS: StockDebtFilters = {
  query: '',
  book: 'all',
  supplierIds: [],
  locations: [],
  locationsMode: 'include',
  dateFrom: '',
  dateTo: '',
  onlyDebt: true,
  projected: false,
};

const BOOKS: StockDebtBook[] = ['all', 'project', 'retail'];
const DAY_MS = 86_400_000;

/** Calendar days from `today` to `iso`, counted on local dates (no DST or UTC shift). */
function daysFromToday(iso: string, today: Date): string {
  const date = parseIsoDate(iso);
  if (!date) return '';
  const target = Date.UTC(date.getFullYear(), date.getMonth(), date.getDate());
  const base = Date.UTC(today.getFullYear(), today.getMonth(), today.getDate());
  return String(Math.round((target - base) / DAY_MS));
}

function dateFromDays(value: string | undefined, today: Date): string {
  if (value === undefined || value.trim() === '') return '';
  const days = Number(value);
  if (!Number.isFinite(days)) return '';
  return toIsoDate(new Date(today.getFullYear(), today.getMonth(), today.getDate() + days)) ?? '';
}

function list(value: string | undefined): string[] {
  return value ? value.split(',').filter(Boolean) : [];
}

export function toSavedViewConfig(filters: StockDebtFilters, today: Date): SavedViewConfig {
  return {
    filters: null,
    sort: [],
    columns: [],
    column_order: [],
    quick_filters: {
      query: filters.query,
      book: filters.book,
      suppliers: filters.supplierIds.join(','),
      locations: filters.locations.join(','),
      locations_mode: filters.locationsMode,
      date_from_days: daysFromToday(filters.dateFrom, today),
      date_to_days: daysFromToday(filters.dateTo, today),
      only_debt: String(filters.onlyDebt),
      projected: String(filters.projected),
    },
  };
}

export function fromSavedView(view: SavedView | null, today: Date): StockDebtFilters {
  const q = view?.view?.quick_filters;
  if (!q) return { ...DEFAULT_STOCK_DEBT_FILTERS };
  const d = DEFAULT_STOCK_DEBT_FILTERS;
  return {
    query: q.query ?? d.query,
    book: BOOKS.includes(q.book as StockDebtBook) ? (q.book as StockDebtBook) : d.book,
    supplierIds: list(q.suppliers),
    locations: list(q.locations),
    locationsMode: q.locations_mode === 'exclude' ? 'exclude' : 'include',
    dateFrom: dateFromDays(q.date_from_days, today),
    dateTo: dateFromDays(q.date_to_days, today),
    onlyDebt: q.only_debt === undefined ? d.onlyDebt : q.only_debt === 'true',
    projected: q.projected === undefined ? d.projected : q.projected === 'true',
  };
}
