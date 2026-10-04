/**
 * PackingListDetailsTab - Regions (REGION-PACKING-LIST, AC-RPL-4).
 *
 * The detail page shows the packing list's Regions in the view layout, as "West Malaysia" /
 * "East Malaysia" (a value is never hidden for being the default).
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';

if (!window.matchMedia) {
  (window as unknown as { matchMedia: unknown }).matchMedia = () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
  });
}
Element.prototype.scrollIntoView = vi.fn();

const record = vi.fn();
vi.mock('../[id]/components/packing-list-context', () => ({
  usePackingListRecord: () => record(),
}));
vi.mock('@/app/(protected)/scm/hooks/useFulfilment', () => ({
  useContainerSizes: () => ({ data: [], isLoading: false }),
}));
vi.mock('./SupplierCombobox', () => ({ SupplierCombobox: () => null }));
vi.mock('@/components/common/ContainerVolumeFill', () => ({ ContainerVolumeFill: () => null }));

import { PackingListDetailsTab } from './PackingListDetailsTab';

function ctx(regions: string[] | undefined, editing = false) {
  return {
    packingList: {
      id: 'pl-1',
      shipment_number: 'PL-2026-0001',
      shipment_date: '2026-08-01',
      shipment_status: 'in_transit',
      shipment_lines: [],
      regions,
    },
    editing,
    draft: {},
    setField: vi.fn(),
    suppliers: [],
    lineSupplierNames: '',
    checkpoints: [],
    canReadScm: false,
  };
}

beforeEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('PackingListDetailsTab - Regions (AC-RPL-4)', () => {
  it('labels the field Regions', () => {
    record.mockReturnValue(ctx(['west']));
    render(<PackingListDetailsTab />);
    expect(screen.getByText('Regions')).toBeInTheDocument();
  });

  it('shows West Malaysia for a West-only packing list', () => {
    record.mockReturnValue(ctx(['west']));
    render(<PackingListDetailsTab />);
    expect(screen.getByText('West Malaysia')).toBeInTheDocument();
    expect(screen.queryByText('East Malaysia')).not.toBeInTheDocument();
  });

  it('shows East Malaysia for an East-only packing list', () => {
    record.mockReturnValue(ctx(['east']));
    render(<PackingListDetailsTab />);
    expect(screen.getByText('East Malaysia')).toBeInTheDocument();
    expect(screen.queryByText('West Malaysia')).not.toBeInTheDocument();
  });

  it('shows both for a West + East packing list', () => {
    record.mockReturnValue(ctx(['west', 'east']));
    render(<PackingListDetailsTab />);
    expect(screen.getByText('West Malaysia')).toBeInTheDocument();
    expect(screen.getByText('East Malaysia')).toBeInTheDocument();
  });
});


describe('PackingListDetailsTab - Regions placement and edit (review round 1)', () => {
  function labelsInOrder(container: HTMLElement) {
    return [...container.querySelectorAll('p.text-muted-foreground')].map((n) => n.textContent);
  }

  it('sits right after Consignee in the Container card', () => {
    record.mockReturnValue(ctx(['west']));
    const { container } = render(<PackingListDetailsTab />);
    const labels = labelsInOrder(container);
    expect(labels).toContain('Consignee');
    expect(labels.indexOf('Regions')).toBe(labels.indexOf('Consignee') + 1);
  });

  it('shows the Regions multi-select with the draft regions while editing', () => {
    const c = ctx(['west'], true);
    record.mockReturnValue({ ...c, draft: { regions: 'west,east' } });
    render(<PackingListDetailsTab />);
    expect(screen.getByText('Regions')).toBeInTheDocument();
    expect(screen.getByText(/West Malaysia/)).toBeInTheDocument();
    expect(screen.getByText(/East Malaysia/)).toBeInTheDocument();
  });
});
