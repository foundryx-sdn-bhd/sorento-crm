/**
 * `PLAN-oi-request-cs-reserve.md` section 6e.2 (round 4), `oi-request-cs-reserve-
 * acceptance-criteria.md` AC-RS-85 (reserve mode) / AC-RS-86 (amend mode).
 *
 * `ReserveLineForm` is a brand-new component this round introduces to replace
 * `ReserveRowDialog`'s per-row `ReserveRowSection` (`ReserveRowDialog.tsx`) - a small
 * dialog whose ONLY job is to hand back a staged decision (`onStage`), never posting
 * anything itself. It does not exist on disk yet.
 *
 * TEST-FIRST (Phase 2): today `./ReserveLineForm` does not resolve - a red here is
 * that missing module (Vite's own "Failed to resolve import"), the plan's own stated
 * component simply not built yet, never an import typo (the relative path matches
 * where `ReserveRowDialog.tsx` already lives, its sibling per the plan).
 */
import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { ReserveLineForm } from './ReserveLineForm';

const LOCATION_OPTIONS = [
  { value: 'wh-brw', label: 'BRW' },
  { value: 'wh-dc1', label: 'DC1' },
];
const AVAILABLE_QTY_BY_LOCATION = { 'wh-brw': 107, 'wh-dc1': 20 };

function renderForm(overrides: Partial<React.ComponentProps<typeof ReserveLineForm>> = {}) {
  const onStage = vi.fn();
  const onOpenChange = vi.fn();
  const utils = render(
    <ReserveLineForm
      open
      onOpenChange={onOpenChange}
      itemCode="B2155-NL-BLUE"
      mode="reserve"
      requestedQty="107"
      locationOptions={LOCATION_OPTIONS}
      availableQtyByLocation={AVAILABLE_QTY_BY_LOCATION}
      defaultLocationId="wh-brw"
      onStage={onStage}
      {...overrides}
    />,
  );
  return { onStage, onOpenChange, ...utils };
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('AC-RS-85: reserve mode - title, Location prefilled, Reserved default, Reason gated, Save', () => {
  it('titles the dialog by the item code and prefills Location + Reserved from the default pool', async () => {
    renderForm();

    expect(await screen.findByRole('dialog', { name: /B2155-NL-BLUE/i })).toBeInTheDocument();
    const reservedInput = screen.getByLabelText('Reserved') as HTMLInputElement;
    // The request itself (owner 16 Oct: never capped by CRM stock).
    expect(reservedInput.value).toBe('107');
  });

  it('Reason appears only when Reserved < Requested, and Stage stays disabled until it is filled', async () => {
    const { onStage } = renderForm();

    expect(screen.queryByLabelText(/reason/i)).not.toBeInTheDocument();
    const stageButton = screen.getByRole('button', { name: /^save$/i });
    expect(stageButton).toBeEnabled();

    fireEvent.change(screen.getByLabelText('Reserved'), { target: { value: '20' } });

    const reasonInput = await screen.findByLabelText(/reason/i);
    expect(screen.getByRole('button', { name: /^save$/i })).toBeDisabled();

    fireEvent.change(reasonInput, { target: { value: 'only 20 available' } });
    await waitFor(() => expect(screen.getByRole('button', { name: /^save$/i })).toBeEnabled());

    fireEvent.click(screen.getByRole('button', { name: /^save$/i }));
    expect(onStage).toHaveBeenCalledWith({
      warehouse_id: 'wh-brw',
      qty_reserved: 20,
      reason: 'only 20 available',
    });
  });

  it('owner 16 Oct: choosing DC1 (20 available) keeps Reserved at the request - CRM stock is information only', async () => {
    const { onStage } = renderForm();

    fireEvent.click(screen.getByLabelText('Location'));
    expect(await screen.findByText('available 20')).toBeInTheDocument();
    fireEvent.click(await screen.findByText('DC1'));

    // AutoCount may already hold the reservation while CRM stock lags: never capped by it.
    expect((screen.getByLabelText('Reserved') as HTMLInputElement).value).toBe('107');
    expect(screen.queryByLabelText(/reason/i)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /^save$/i }));
    expect(onStage).toHaveBeenCalledWith({ warehouse_id: 'wh-dc1', qty_reserved: 107, reason: null });
  });

  it('owner 16 Oct: prefills the full request even when the pool shows less (SRT53-CR: 300 asked, 28 in CRM)', () => {
    renderForm({ requestedQty: '300', availableQtyByLocation: { 'wh-brw': 28, 'wh-dc1': 0 } });
    expect((screen.getByLabelText('Reserved') as HTMLInputElement).value).toBe('300');
    expect(screen.queryByLabelText(/reason/i)).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Reserved'), { target: { value: '400' } });
    // NUMBER-FIELDS: what was typed stays as typed; the limit is checked on Save.
    expect((screen.getByLabelText('Reserved') as HTMLInputElement).value).toBe('400');
  });

  it('no button says Stage', () => {
    renderForm();
    expect(screen.queryByRole('button', { name: /stage/i })).not.toBeInTheDocument();
  });

  it('6e.4 (S8): with no location chosen, Reserved prefills the request and the stage carries no warehouse_id', async () => {
    const { onStage } = renderForm({ locationOptions: [], availableQtyByLocation: {}, defaultLocationId: null });

    expect((screen.getByLabelText('Reserved') as HTMLInputElement).value).toBe('107');
    fireEvent.click(screen.getByRole('button', { name: /^save$/i }));
    expect(onStage).toHaveBeenCalledWith({ qty_reserved: 107, reason: null });
  });
});

describe('AC-RS-86: amend mode - Location is locked read-only text, Reserved prefilled with the net', () => {
  it('renders the locked location as plain text, no SearchableSelect', async () => {
    renderForm({
      mode: 'amend',
      lockedLocationLabel: 'BRW',
      initialQty: '30',
      requestedQty: '50',
    });

    expect(await screen.findByRole('dialog', { name: /B2155-NL-BLUE/i })).toBeInTheDocument();
    expect(screen.queryByLabelText('Location')).not.toBeInTheDocument();
    expect(screen.getByText('BRW')).toBeInTheDocument();

    const reservedInput = screen.getByLabelText('Reserved') as HTMLInputElement;
    expect(reservedInput.value).toBe('30');
  });

  it('6e.4 (S1): amend mode requires a reason when short of the request too', async () => {
    renderForm({ mode: 'amend', lockedLocationLabel: 'BRW', initialQty: '30', requestedQty: '50' });

    expect(screen.getByLabelText(/reason/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^save$/i })).toBeDisabled();
    fireEvent.change(screen.getByLabelText(/reason/i), { target: { value: 'BRW has 30' } });
    expect(screen.getByRole('button', { name: /^save$/i })).toBeEnabled();
  });

  it('6e.4: amend mode edits the line net - may go above one request, up to maxQty; sub-label for several requests', async () => {
    const { onStage } = renderForm({
      mode: 'amend',
      lockedLocationLabel: 'BRW',
      initialQty: '30',
      requestedQty: '50',
      maxQty: '60',
      answeredRequestCount: 2,
    });

    expect(screen.getByText('Requested 50 across 2 requests')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Reserved'), { target: { value: '55' } });
    expect((screen.getByLabelText('Reserved') as HTMLInputElement).value).toBe('55');
    expect(screen.queryByLabelText(/reason/i)).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Reserved'), { target: { value: '90' } });
    // NUMBER-FIELDS: kept as typed; Save is blocked with the sentence, nothing staged.
    expect((screen.getByLabelText('Reserved') as HTMLInputElement).value).toBe('90');
    fireEvent.click(screen.getByRole('button', { name: /^save$/i }));
    expect(onStage).not.toHaveBeenCalled();
    expect(screen.getByText('Enter a number between 0 and 60.')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Reserved'), { target: { value: '60' } });
    expect(screen.queryByText('Enter a number between 0 and 60.')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /^save$/i }));
    expect(onStage).toHaveBeenCalledWith({ qty_reserved: 60, reason: null });
  });

  it('Stage in amend mode calls onStage with no warehouse_id change, only the new qty', async () => {
    const { onStage } = renderForm({
      mode: 'amend',
      lockedLocationLabel: 'BRW',
      initialQty: '30',
      requestedQty: '50',
    });

    fireEvent.change(screen.getByLabelText('Reserved'), { target: { value: '10' } });
    fireEvent.change(await screen.findByLabelText(/reason/i), { target: { value: 'reduced' } });
    fireEvent.click(screen.getByRole('button', { name: /^save$/i }));

    expect(onStage).toHaveBeenCalledWith(
      expect.objectContaining({ qty_reserved: 10, reason: 'reduced' }),
    );
  });
});
