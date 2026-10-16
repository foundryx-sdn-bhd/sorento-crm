/**
 * STOCK-DEBT-SAVED-VIEWS AC-V2 / AC-V3 / AC-V4: the pure mapping between the board's
 * filters and a saved view's `quick_filters`. The delivery date range is stored as whole
 * days from TODAY (local date), never as a fixed date, so a view saved on 14 Oct and applied
 * on 20 Oct reads 20 Oct to 19 Nov.
 */
import { describe, expect, it } from 'vitest';
import type { SavedView } from '@/services/savedViewsService';
import {
  DEFAULT_STOCK_DEBT_FILTERS,
  STOCK_DEBT_LISTING_KEY,
  fromSavedView,
  toSavedViewConfig,
  type StockDebtFilters,
} from './stockDebtSavedView';

// Pin a zone east of UTC so a late-night-local `today` is still YESTERDAY in UTC. A runner on
// UTC would otherwise never tell a local-date computation from a `toISOString` one.
process.env.TZ = 'Asia/Kuala_Lumpur';

const OCT_14 = new Date(2026, 9, 14);
const OCT_20 = new Date(2026, 9, 20);

function filters(overrides: Partial<StockDebtFilters> = {}): StockDebtFilters {
  return { ...DEFAULT_STOCK_DEBT_FILTERS, ...overrides };
}

function view(quick_filters: Record<string, string> | null | undefined): SavedView {
  return {
    id: 'v1',
    name: 'My view',
    is_shared: false,
    is_default: false,
    owner_name: null,
    view: {
      filters: null,
      sort: [],
      columns: [],
      column_order: [],
      quick_filters: quick_filters as Record<string, string> | null,
    },
  };
}

describe('constants', () => {
  it('uses the stock debt listing key', () => {
    expect(STOCK_DEBT_LISTING_KEY).toBe('projects.stock_debt.view::stock-debt');
  });

  it('defaults to the screen defaults', () => {
    expect(DEFAULT_STOCK_DEBT_FILTERS).toEqual({
      query: '',
      book: 'all',
      supplierIds: [],
      locations: [],
      locationsMode: 'include',
      dateFrom: '',
      dateTo: '',
      onlyDebt: true,
      projected: false,
    });
  });
});

describe('toSavedViewConfig', () => {
  it('stores the date range as whole days from today', () => {
    const config = toSavedViewConfig(
      filters({ dateFrom: '2026-10-14', dateTo: '2026-11-13' }),
      OCT_14,
    );
    expect(config.quick_filters?.date_from_days).toBe('0');
    expect(config.quick_filters?.date_to_days).toBe('30');
  });

  it('leaves filters null and sort, columns, column_order empty', () => {
    const config = toSavedViewConfig(filters(), OCT_14);
    expect(config.filters).toBeNull();
    expect(config.sort).toEqual([]);
    expect(config.columns).toEqual([]);
    expect(config.column_order).toEqual([]);
  });

  it('stores an open end as an empty string', () => {
    const config = toSavedViewConfig(filters(), OCT_14);
    expect(config.quick_filters?.date_from_days).toBe('');
    expect(config.quick_filters?.date_to_days).toBe('');
  });

  it('stores a negative offset for a start in the past', () => {
    const config = toSavedViewConfig(filters({ dateFrom: '2026-10-07' }), OCT_14);
    expect(config.quick_filters?.date_from_days).toBe('-7');
    expect(config.quick_filters?.date_to_days).toBe('');
  });

  it('counts days across a month and year rollover', () => {
    const config = toSavedViewConfig(
      filters({ dateFrom: '2026-12-31', dateTo: '2027-01-01' }),
      new Date(2026, 11, 31),
    );
    expect(config.quick_filters?.date_from_days).toBe('0');
    expect(config.quick_filters?.date_to_days).toBe('1');
  });

  it('joins lists with commas and writes booleans as true/false strings', () => {
    const config = toSavedViewConfig(
      filters({
        query: 'basin',
        book: 'project',
        supplierIds: ['s1', 'none'],
        locations: ['w-1', 'w-2'],
        onlyDebt: false,
        projected: true,
      }),
      OCT_14,
    );
    expect(config.quick_filters).toMatchObject({
      query: 'basin',
      book: 'project',
      suppliers: 's1,none',
      locations: 'w-1,w-2',
      only_debt: 'false',
      projected: 'true',
    });
  });

  it('never stores a fixed date', () => {
    const config = toSavedViewConfig(
      filters({ dateFrom: '2026-10-14', dateTo: '2026-11-13', query: 'x' }),
      OCT_14,
    );
    for (const value of Object.values(config.quick_filters ?? {})) {
      expect(value).not.toMatch(/\d{4}-\d{2}-\d{2}/);
    }
  });
});

describe('fromSavedView', () => {
  it('recomputes the range from today: saved 14 Oct, applied 20 Oct', () => {
    const saved = toSavedViewConfig(
      filters({ dateFrom: '2026-10-14', dateTo: '2026-11-13' }),
      OCT_14,
    );
    const applied = fromSavedView(view(saved.quick_filters), OCT_20);
    expect(applied.dateFrom).toBe('2026-10-20');
    expect(applied.dateTo).toBe('2026-11-19');
  });

  it('applies a negative offset', () => {
    const applied = fromSavedView(view({ date_from_days: '-7', date_to_days: '' }), OCT_14);
    expect(applied.dateFrom).toBe('2026-10-07');
    expect(applied.dateTo).toBe('');
  });

  it('rolls over month and year in local dates', () => {
    const applied = fromSavedView(
      view({ date_from_days: '0', date_to_days: '1' }),
      new Date(2026, 11, 31, 23, 30),
    );
    expect(applied.dateFrom).toBe('2026-12-31');
    expect(applied.dateTo).toBe('2027-01-01');
  });

  it('keeps open ends open', () => {
    const applied = fromSavedView(view({ date_from_days: '', date_to_days: '' }), OCT_14);
    expect(applied.dateFrom).toBe('');
    expect(applied.dateTo).toBe('');
  });

  it('round-trips every other field', () => {
    const original = filters({
      query: 'basin',
      book: 'retail',
      supplierIds: ['s1', 'none'],
      locations: ['w-1', 'w-2'],
      onlyDebt: false,
      projected: true,
      dateFrom: '2026-10-14',
      dateTo: '2026-11-13',
    });
    const back = fromSavedView(view(toSavedViewConfig(original, OCT_14).quick_filters), OCT_14);
    expect(back).toEqual(original);
  });

  it('turns empty list strings into empty arrays', () => {
    const applied = fromSavedView(view({ suppliers: '', locations: '' }), OCT_14);
    expect(applied.supplierIds).toEqual([]);
    expect(applied.locations).toEqual([]);
  });

  it('falls back to the screen default for a missing key', () => {
    const applied = fromSavedView(view({ book: 'project' }), OCT_14);
    expect(applied).toEqual({ ...DEFAULT_STOCK_DEBT_FILTERS, book: 'project' });
  });

  it('returns the defaults for null quick_filters or a null view', () => {
    expect(fromSavedView(view(null), OCT_14)).toEqual(DEFAULT_STOCK_DEBT_FILTERS);
    expect(fromSavedView(view(undefined), OCT_14)).toEqual(DEFAULT_STOCK_DEBT_FILTERS);
    expect(fromSavedView(null, OCT_14)).toEqual(DEFAULT_STOCK_DEBT_FILTERS);
  });
});

describe('local dates, not UTC dates', () => {
  // 01:00 on 14 Oct in Kuala Lumpur is 17:00 on 13 Oct in UTC.
  const earlyLocal = new Date(2026, 9, 14, 1, 0);

  it('the pinned zone is in effect', () => {
    expect(earlyLocal.getUTCDate()).toBe(13);
  });

  it('applies offsets from the LOCAL day', () => {
    const applied = fromSavedView(view({ date_from_days: '0', date_to_days: '30' }), earlyLocal);
    expect(applied.dateFrom).toBe('2026-10-14');
    expect(applied.dateTo).toBe('2026-11-13');
  });

  it('saves offsets from the LOCAL day', () => {
    const config = toSavedViewConfig(
      filters({ dateFrom: '2026-10-14', dateTo: '2026-11-13' }),
      earlyLocal,
    );
    expect(config.quick_filters?.date_from_days).toBe('0');
    expect(config.quick_filters?.date_to_days).toBe('30');
  });
});

describe('locations_mode (AC-R10)', () => {
  it('saves the mode beside locations', () => {
    const config = toSavedViewConfig(
      filters({ locations: ['w-9'], locationsMode: 'exclude' }),
      OCT_14,
    );
    expect(config.quick_filters).toMatchObject({ locations: 'w-9', locations_mode: 'exclude' });
    expect(toSavedViewConfig(filters(), OCT_14).quick_filters?.locations_mode).toBe('include');
  });

  it('never writes the retired group keys', () => {
    const keys = Object.keys(toSavedViewConfig(filters(), OCT_14).quick_filters ?? {});
    expect(keys).not.toContain('groups');
    expect(keys).not.toContain('groups_mode');
  });

  it('round-trips exclude', () => {
    const original = filters({ locations: ['w-1', 'w-2'], locationsMode: 'exclude' });
    const back = fromSavedView(view(toSavedViewConfig(original, OCT_14).quick_filters), OCT_14);
    expect(back.locationsMode).toBe('exclude');
    expect(back.locations).toEqual(['w-1', 'w-2']);
  });

  it('a view with no locations key applies as Every location, Only', () => {
    const applied = fromSavedView(view({ book: 'all' }), OCT_14);
    expect(applied.locations).toEqual([]);
    expect(applied.locationsMode).toBe('include');
    expect(fromSavedView(null, OCT_14).locationsMode).toBe('include');
  });

  it('a view saved with only the old groups keys applies as Every location, Only', () => {
    const applied = fromSavedView(view({ groups: 'ED', groups_mode: 'exclude' }), OCT_14);
    expect(applied.locations).toEqual([]);
    expect(applied.locationsMode).toBe('include');
  });
});
