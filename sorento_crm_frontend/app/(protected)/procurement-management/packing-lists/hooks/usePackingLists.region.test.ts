/**
 * The detail page's pager rebuilds the list query from the URL (REGION-PACKING-LIST,
 * review round 1): `region` must survive that round trip, or prev/next walks a different
 * set than the filtered list the user came from.
 */
import { describe, it, expect } from 'vitest';
import {
  packingListsListParamsFromUrl,
  packingListsListQueryKey,
} from './usePackingLists';

const base = { pageIndex: 0, pageSize: 50, sorting: [], searchQuery: '' };

describe('packing lists pager query', () => {
  it('reads region back off the detail URL filters', () => {
    const params = packingListsListParamsFromUrl({ ...base, filters: { region: 'east' } });
    expect(params.region).toBe('east');
  });

  it('keys the page by region, so it reads the page the filtered list fetched', () => {
    const fromUrl = packingListsListParamsFromUrl({ ...base, filters: { region: 'east' } });
    expect(packingListsListQueryKey(fromUrl)).toEqual(
      packingListsListQueryKey({ ...base, region: 'east' }),
    );
    expect(packingListsListQueryKey(fromUrl)).not.toEqual(packingListsListQueryKey(base));
  });
});
