/**
 * Stage 1C - the Supply composition section and the one press that commits it (J04-J05).
 *
 * The whole sales order is the unit: one Confirm, no per-line action, and no partial state
 * to come back to. A line that does not balance, a Borrow with no reason or a discontinued
 * Buy with none blocks the single Confirm and says which line it is (AC-B09, AC-B11,
 * AC-C01). A refused confirmation writes nothing and names every line that refused it, on
 * the sheet rather than in a toast that has already gone (AC-C02). A revision that no
 * longer matches the order says so (AC-C06).
 */
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type {
  SupplyLine,
  SupplyProposal,
} from '../../_shared/types/fulfilmentPlanning.types';

if (!window.matchMedia) {
  (window as unknown as { matchMedia: unknown }).matchMedia = () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
  });
}

const getSupply = vi.fn();
const confirmSupply = vi.fn();

// The real module, with its two calls replaced: the section reads ConfirmSupplyError off
// it to tell a refusal from any other failure, and a stand-in class would not be that one.
vi.mock('../../_shared/services/fulfilmentPlanningService', async (importOriginal) => {
  const actual =
    await importOriginal<typeof import('../../_shared/services/fulfilmentPlanningService')>();
  return {
    ...actual,
    getSupply: (...args: unknown[]) => getSupply(...args),
    confirmSupply: (...args: unknown[]) => confirmSupply(...args),
  };
});

const toastSuccess = vi.fn();
const toastError = vi.fn();
vi.mock('@/lib/toast', () => ({
  toast: {
    success: (...args: unknown[]) => toastSuccess(...args),
    warning: vi.fn(),
    error: (...args: unknown[]) => toastError(...args),
  },
}));

import { ConfirmSupplyError } from '../../_shared/services/fulfilmentPlanningService';
import { SUPPLY_KEY } from '../../_shared/hooks/useFulfilmentPlanning';
import { SupplyCompositionSection } from './SupplyCompositionSection';

const PSO_ID = 'c7b2a3f1-2222-4b22-9b22-222222222222';
const PROJECT_ID = 'b6a1f2e0-1111-4a11-8a11-111111111111';
const WH_BRW = 'a1000000-0000-4000-8000-000000000001';
const WH_HQ = 'a1000000-0000-4000-8000-000000000002';

function line(overrides: Partial<SupplyLine> = {}): SupplyLine {
  return {
    project_line_id: 'd4000000-0000-4000-8000-000000000001',
    line_no: 1,
    item_code: 'CB6633',
    description: 'CABANA S/STEEL FLOOR GRATING 6"',
    uom: 'UNIT',
    open_qty: '600',
    required_date: '2026-09-01',
    fulfilment_location: 'BRW-BB',
    is_dealer_hot_selling: false,
    is_project_hot_selling: false,
    dealer_classified: false,
    project_classified: false,
    classification_unavailable: false,
    is_discontinued: false,
    pool_location: 'BRW-BB',
    pool_cap: null,
    pool_reorder_level: '120',
    components: [
      {
        kind: 'reserve',
        qty: '600',
        reason: 'Free stock at BRW-BB covers the need by the delivery date.',
        source_location: 'BRW-BB',
        source_warehouse_id: WH_BRW,
      },
    ],
    timely_spo: [],
    advisory_spo: [],
    borrow_candidates: [],
    ...overrides,
  };
}

function proposal(overrides: Partial<SupplyProposal> = {}): SupplyProposal {
  return {
    project_sales_order_id: PSO_ID,
    provisional_ref: 'PSO-000123',
    autocount_doc_no: 'SO376201',
    project_id: PROJECT_ID,
    project_code: 'PRJ-0041',
    project_name: 'Tuju Residences',
    status: 'published',
    review_state: 'needs_cs_review',
    lines: [line()],
    ...overrides,
  };
}

const CONFIRMED = proposal({
  review_state: 'confirmed',
  decision: {
    revision_no: 2,
    state: 'active',
    confirmed_by_name: 'Nurul Aina',
    confirmed_at: '2026-08-16T09:30:00',
  },
  lines: [
    // The backend answers a confirmed order with BOTH: the live proposal, which is what
    // composing again starts from, and the composition the revision froze.
    line({
      frozen: {
        open_qty: '600',
        components: [
          {
            kind: 'reserve',
            qty: '600',
            reason: 'Free stock at BRW-BB covers the need by the delivery date.',
            source_location: 'BRW-BB',
            source_warehouse_id: WH_BRW,
          },
        ],
      },
    }),
  ],
});

function renderSection(open = true) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  });
  const rendered = render(
    <QueryClientProvider client={client}>
      <SupplyCompositionSection psoId={PSO_ID} reference="SO376201" open={open} />
    </QueryClientProvider>,
  );
  return { ...rendered, client };
}

/** A refetch of the composition under the same revision, as an invalidation elsewhere causes. */
async function refetchSupply(client: QueryClient) {
  await act(async () => {
    await client.refetchQueries({ queryKey: [SUPPLY_KEY, PSO_ID] });
  });
}

const LINE_2 = line({
  project_line_id: 'd4000000-0000-4000-8000-000000000002',
  line_no: 2,
  item_code: 'SRT501-CP',
  open_qty: '70',
  components: [{ kind: 'buy', qty: '70', reason: 'Remaining uncovered need.' }],
});

const UNPLANNABLE = line({
  project_line_id: 'd4000000-0000-4000-8000-000000000003',
  line_no: 3,
  item_code: 'SRT770-BK',
  open_qty: '0',
  unplannable_reason: 'No reconciled AutoCount line, so there is no open quantity to promise.',
  components: [],
});

const confirmButton = () => screen.getByRole('button', { name: 'Confirm Project SO' });

beforeEach(() => {
  vi.clearAllMocks();
  getSupply.mockResolvedValue(proposal());
  confirmSupply.mockResolvedValue({
    revision_no: 1,
    confirmed_at: '2026-08-18T02:00:00',
    review_state: 'confirmed',
    inquiry_rows_created: 1,
    exceptions: [],
  });
});

describe('SupplyCompositionSection', () => {
  it('asks for nothing while the sheet is closed', () => {
    renderSection(false);

    expect(getSupply).not.toHaveBeenCalled();
  });

  it('shows a placeholder while the composition is being read', () => {
    getSupply.mockReturnValue(new Promise(() => {}));

    const { container } = renderSection();

    expect(container.querySelectorAll('[data-slot="skeleton"]').length).toBeGreaterThan(0);
  });

  it('states a failed read in words and offers a retry', async () => {
    getSupply.mockRejectedValue(new Error('The stock service is down'));

    renderSection();

    expect(
      await screen.findByText('The supply composition could not be loaded'),
    ).toBeInTheDocument();
    expect(screen.getByText('The stock service is down')).toBeInTheDocument();

    getSupply.mockResolvedValue(proposal());
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));

    await screen.findByText('Line 1 · CB6633');
  });

  it('says there is nothing to compose rather than an empty section', async () => {
    getSupply.mockResolvedValue(proposal({ lines: [] }));

    renderSection();

    expect(await screen.findByText('There is nothing to compose')).toBeInTheDocument();
    expect(
      screen.getByText('This sales order carries no line with an open quantity.'),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: 'Confirm Project SO' }),
    ).not.toBeInTheDocument();
  });

  it('renders one card per line, with the engine proposal already in it', async () => {
    getSupply.mockResolvedValue(
      proposal({
        lines: [
          line(),
          line({
            project_line_id: 'd4000000-0000-4000-8000-000000000002',
            line_no: 2,
            item_code: 'SRT501-CP',
            open_qty: '70',
            components: [{ kind: 'buy', qty: '70', reason: 'Remaining uncovered need.' }],
          }),
        ],
      }),
    );

    renderSection();

    expect(await screen.findByText('Line 1 · CB6633')).toBeInTheDocument();
    expect(screen.getByText('Line 2 · SRT501-CP')).toBeInTheDocument();
    expect(confirmButton()).toBeEnabled();
  });

  // ------------------------------------------- the line set moving under the same revision
  it('renders a line a refetch adds, and keeps what was typed on the lines it kept', async () => {
    const { client } = renderSection();
    await screen.findByText('Line 1 · CB6633');
    fireEvent.change(screen.getByLabelText('Buy on line 1'), { target: { value: '300' } });

    getSupply.mockResolvedValue(proposal({ lines: [line(), LINE_2] }));
    await refetchSupply(client);

    expect(await screen.findByText('Line 2 · SRT501-CP')).toBeInTheDocument();
    expect(screen.getByLabelText('Buy on line 1')).toHaveValue(300);
    expect(screen.getByLabelText('Buy on line 2')).toHaveValue(70);
  });

  it('posts the drafts after a dropped line against their own line ids', async () => {
    getSupply.mockResolvedValue(proposal({ lines: [line(), LINE_2] }));
    const { client } = renderSection();
    await screen.findByText('Line 2 · SRT501-CP');
    fireEvent.change(screen.getByLabelText('Buy on line 2'), { target: { value: '70' } });

    getSupply.mockResolvedValue(proposal({ lines: [LINE_2] }));
    await refetchSupply(client);
    await waitFor(() => expect(screen.queryByText('Line 1 · CB6633')).not.toBeInTheDocument());

    fireEvent.click(confirmButton());
    fireEvent.click(
      within(await screen.findByRole('alertdialog')).getByRole('button', {
        name: 'Confirm the sales order',
      }),
    );

    await waitFor(() => expect(confirmSupply).toHaveBeenCalledTimes(1));
    const body = confirmSupply.mock.calls[0][1] as { lines: { project_line_id: string }[] };
    expect(body.lines.map((entry) => entry.project_line_id)).toEqual([LINE_2.project_line_id]);
  });

  // ------------------------------------------------------- an unplannable line
  it('shows an unplannable line blocked with its reason, and confirms the rest without it', async () => {
    getSupply.mockResolvedValue(proposal({ lines: [line(), UNPLANNABLE] }));
    renderSection();
    await screen.findByText('Line 3 · SRT770-BK');

    expect(
      screen.getByText('No reconciled AutoCount line, so there is no open quantity to promise.'),
    ).toBeInTheDocument();
    expect(screen.queryByLabelText('Buy on line 3')).not.toBeInTheDocument();
    // Not a blocker: it is left out of the confirmation, not holding it up.
    expect(screen.queryByText(/Line 3, SRT770-BK/)).not.toBeInTheDocument();
    expect(confirmButton()).toBeEnabled();

    fireEvent.click(confirmButton());
    const dialog = within(await screen.findByRole('alertdialog'));
    expect(dialog.getByText('All 1 line are confirmed together.')).toBeInTheDocument();
    fireEvent.click(dialog.getByRole('button', { name: 'Confirm the sales order' }));

    await waitFor(() => expect(confirmSupply).toHaveBeenCalledTimes(1));
    const body = confirmSupply.mock.calls[0][1] as { lines: { project_line_id: string }[] };
    expect(body.lines.map((entry) => entry.project_line_id)).toEqual([
      'd4000000-0000-4000-8000-000000000001',
    ]);
  });

  it('disables the Confirm when every line is unplannable: there is nothing it could name', async () => {
    getSupply.mockResolvedValue(proposal({ lines: [UNPLANNABLE] }));
    renderSection();
    await screen.findByText('Line 3 · SRT770-BK');

    expect(confirmButton()).toBeDisabled();
  });

  // ------------------------------------------------------------------ blockers
  it('blocks the one Confirm while any line does not balance, and says which', async () => {
    renderSection();
    await screen.findByText('Line 1 · CB6633');

    fireEvent.change(screen.getByLabelText('Buy on line 1'), { target: { value: '300' } });

    // Once on the card it belongs to, once at the foot beside the Confirm it blocks.
    expect(
      await screen.findAllByText(
        'Line 1, CB6633: the components are over the open quantity by 300.',
      ),
    ).toHaveLength(2);
    expect(confirmButton()).toBeDisabled();
  });

  it('blocks the Confirm while a borrow on any line has no reason (AC-B09)', async () => {
    // The engine's own borrow OPENS with the engine's sentence in the box (the board has
    // always seeded it that way), so an untouched proposal confirms without anybody
    // retyping it. AC-B09 is still pinned, on the case it is actually about: a borrow left
    // with no reason at all cannot be written.
    getSupply.mockResolvedValue(
      proposal({
        lines: [
          line({
            components: [
              {
                kind: 'borrow',
                qty: '600',
                reason: 'Free stock at HQ, outside the reserve pool for this location.',
                source_location: 'HQ',
                source_warehouse_id: WH_HQ,
              },
            ],
          }),
        ],
      }),
    );

    renderSection();
    await screen.findByText('Line 1 · CB6633');

    const reason = screen.getByLabelText(/^Reason/) as HTMLTextAreaElement;
    expect(reason.value).toBe(
      'Free stock at HQ, outside the reserve pool for this location.',
    );
    expect(confirmButton()).toBeEnabled();

    fireEvent.change(reason, { target: { value: '   ' } });

    await waitFor(() => expect(confirmButton()).toBeDisabled());
    expect(
      screen.getAllByText('Line 1, CB6633: the borrow from HQ needs a reason.').length,
    ).toBeGreaterThan(0);

    fireEvent.change(reason, {
      target: { value: 'HQ has no delivery booked before October.' },
    });

    await waitFor(() => expect(confirmButton()).toBeEnabled());
  });

  // D2 (S1, PR #1218 review gap): AC-B11's gate was removed from `supplyComposition.ts`'s
  // `lineBlockers` (the owner's "remove that gate" ruling) - a discontinued Buy with no
  // reason no longer blocks Confirm here, matching AC-22's own claim for this sheet.
  it('does not block the Confirm for a discontinued buy with no reason (AC-22)', async () => {
    getSupply.mockResolvedValue(
      proposal({
        lines: [
          line({
            is_discontinued: true,
            open_qty: '25',
            components: [{ kind: 'buy', qty: '25', reason: 'Remaining uncovered need.' }],
          }),
        ],
      }),
    );

    renderSection();
    await screen.findByText('Line 1 · CB6633');

    await waitFor(() => expect(confirmButton()).toBeEnabled());
  });

  // --------------------------------------------------------------- confirming
  it('asks before it confirms, naming the sales order and its line count (AC-G03)', async () => {
    renderSection();
    await screen.findByText('Line 1 · CB6633');

    fireEvent.click(confirmButton());

    const dialog = within(await screen.findByRole('alertdialog'));
    expect(dialog.getByText('Confirm SO376201?')).toBeInTheDocument();
    expect(dialog.getByText('All 1 line are confirmed together.')).toBeInTheDocument();
    expect(confirmSupply).not.toHaveBeenCalled();
  });

  it('sends every line in one call, in the payload the backend takes (AC-C01)', async () => {
    renderSection();
    await screen.findByText('Line 1 · CB6633');

    fireEvent.click(confirmButton());
    fireEvent.click(
      within(await screen.findByRole('alertdialog')).getByRole('button', {
        name: 'Confirm the sales order',
      }),
    );

    await waitFor(() => expect(confirmSupply).toHaveBeenCalledTimes(1));
    expect(confirmSupply).toHaveBeenCalledWith(PSO_ID, {
      lines: [
        {
          project_line_id: 'd4000000-0000-4000-8000-000000000001',
          timely_spo_qty: '0',
          reserve: [{ warehouse_id: WH_BRW, qty: '600' }],
          borrow: [],
          buy_qty: '0',
          buy_reason: null,
        },
      ],
    });
  });

  it('celebrates the revision and how many purchase rows were handed over', async () => {
    renderSection();
    await screen.findByText('Line 1 · CB6633');

    fireEvent.click(confirmButton());
    fireEvent.click(
      within(await screen.findByRole('alertdialog')).getByRole('button', {
        name: 'Confirm the sales order',
      }),
    );

    await waitFor(() =>
      expect(toastSuccess).toHaveBeenCalledWith(
        'Confirmed as revision 1. 1 purchase row handed over.',
      ),
    );
  });

  // ---------------------------------------------------------------- a refusal
  it('names every line that refused the confirmation, and says nothing was written', async () => {
    confirmSupply.mockRejectedValue(
      new ConfirmSupplyError('This sales order could not be confirmed', [
        {
          line_no: 1,
          item_code: 'CB6633',
          reason: 'Only 25 of the 600 reserved units are still free at BRW-BB.',
        },
        {
          line_no: 2,
          item_code: 'CB2201',
          reason: 'The open quantity changed to 80 after this sheet was opened.',
        },
      ]),
    );

    renderSection();
    await screen.findByText('Line 1 · CB6633');

    fireEvent.click(confirmButton());
    fireEvent.click(
      within(await screen.findByRole('alertdialog')).getByRole('button', {
        name: 'Confirm the sales order',
      }),
    );

    expect(await screen.findByText('Nothing was written')).toBeInTheDocument();
    expect(screen.getByText('Line 1, CB6633:')).toBeInTheDocument();
    expect(
      screen.getByText('Only 25 of the 600 reserved units are still free at BRW-BB.'),
    ).toBeInTheDocument();
    expect(screen.getByText('Line 2, CB2201:')).toBeInTheDocument();
    // The message goes to the toast; the list belongs beside the lines.
    expect(toastError).toHaveBeenCalledWith('This sales order could not be confirmed');
  });

  it('names the sales order itself when a refusal carries no line number', async () => {
    confirmSupply.mockRejectedValue(
      new ConfirmSupplyError('This sales order could not be confirmed', [
        { reason: 'This sales order was amended while the sheet was open.' },
      ]),
    );

    renderSection();
    await screen.findByText('Line 1 · CB6633');

    fireEvent.click(confirmButton());
    fireEvent.click(
      within(await screen.findByRole('alertdialog')).getByRole('button', {
        name: 'Confirm the sales order',
      }),
    );

    expect(await screen.findByText('This sales order:')).toBeInTheDocument();
  });

  it('clears the failing lines when the next attempt is made', async () => {
    confirmSupply.mockRejectedValueOnce(
      new ConfirmSupplyError('This sales order could not be confirmed', [
        { line_no: 1, item_code: 'CB6633', reason: 'Only 25 units are still free.' },
      ]),
    );

    renderSection();
    await screen.findByText('Line 1 · CB6633');

    fireEvent.click(confirmButton());
    fireEvent.click(
      within(await screen.findByRole('alertdialog')).getByRole('button', {
        name: 'Confirm the sales order',
      }),
    );
    expect(await screen.findByText('Nothing was written')).toBeInTheDocument();

    fireEvent.click(confirmButton());
    fireEvent.click(
      within(await screen.findByRole('alertdialog')).getByRole('button', {
        name: 'Confirm the sales order',
      }),
    );

    await waitFor(() => expect(screen.queryByText('Nothing was written')).not.toBeInTheDocument());
  });

  // ------------------------------------------------------------ the confirmed
  it('reads a confirmed order as held by its revision, and offers the order inquiry', async () => {
    getSupply.mockResolvedValue(CONFIRMED);

    renderSection();

    expect(await screen.findByText('Revision 2')).toBeInTheDocument();
    expect(screen.getByText(/by Nurul Aina on 16\/08\/2026/)).toBeInTheDocument();
    expect(screen.getByText('All 1 line are held by revision 2.')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /open the order inquiry/i })).toHaveAttribute(
      'href',
      `/project-sales/${PROJECT_ID}/order-inquiries`,
    );
    expect(
      screen.queryByRole('button', { name: 'Confirm Project SO' }),
    ).not.toBeInTheDocument();
  });

  it('composes again on request, and names the revision the next press would supersede', async () => {
    getSupply.mockResolvedValue(CONFIRMED);

    renderSection();
    fireEvent.click(await screen.findByRole('button', { name: 'Compose again' }));

    // The editor is back, and the way out of it keeps the revision that stands.
    expect(screen.getByLabelText('Buy on line 1')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Keep revision 2' })).toBeInTheDocument();

    fireEvent.click(confirmButton());

    const dialog = within(await screen.findByRole('alertdialog'));
    expect(
      dialog.getByText('All 1 line are confirmed together and supersede revision 2.'),
    ).toBeInTheDocument();
  });

  it('goes back to the frozen view when the revision is kept', async () => {
    getSupply.mockResolvedValue(CONFIRMED);

    renderSection();
    fireEvent.click(await screen.findByRole('button', { name: 'Compose again' }));
    fireEvent.click(screen.getByRole('button', { name: 'Keep revision 2' }));

    expect(await screen.findByText('All 1 line are held by revision 2.')).toBeInTheDocument();
    expect(screen.queryByLabelText('Buy on line 1')).not.toBeInTheDocument();
  });

  // --------------------------------------------------------------- the banners
  it('says a challenged revision no longer matches the order, and why (AC-C06)', async () => {
    getSupply.mockResolvedValue(
      proposal({
        decision: {
          revision_no: 1,
          state: 'challenged',
          confirmed_by_name: 'Nurul Aina',
          confirmed_at: '2026-08-15T04:10:00',
          challenged_reason:
            'Line 1 was amended from 120 to 150 after this revision was confirmed.',
        },
      }),
    );

    renderSection();

    expect(
      await screen.findByText('Revision 1 no longer matches this sales order'),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        'Line 1 was amended from 120 to 150 after this revision was confirmed.',
      ),
    ).toBeInTheDocument();
    // Challenged is not confirmed: the order is composable again.
    expect(confirmButton()).toBeEnabled();
  });

  it('states a challenge with no reason given rather than an empty banner', async () => {
    getSupply.mockResolvedValue(
      proposal({
        decision: { revision_no: 1, state: 'challenged', challenged_reason: null },
      }),
    );

    renderSection();

    expect(
      await screen.findByText(
        'A fact this revision was built on has changed since it was decided.',
      ),
    ).toBeInTheDocument();
  });

  it('says a superseded revision was superseded, and what to do about it', async () => {
    getSupply.mockResolvedValue(
      proposal({
        decision: { revision_no: 1, state: 'superseded' },
      }),
    );

    renderSection();

    expect(await screen.findByText('Revision 1 was superseded')).toBeInTheDocument();
    expect(
      screen.getByText('Compose this sales order again to replace it.'),
    ).toBeInTheDocument();
  });

  // ------------------------------------- site pool share beside a Buy (B2, fix round 5)
  it('admits a site pool share beside a Buy, using the line s own pool_allowances', async () => {
    // SO419208 line 3, the engine's own composition: BRW lends its share (62, within the
    // pool's own allowance of 62 and the five-pool net of 100) and the rest is bought.
    // `lineBlockers` used to run with no `limits` at all on this sheet, so it read the split
    // as the whole-line rule's forbidden mix and blocked Confirm for the whole order.
    getSupply.mockResolvedValue(
      proposal({
        lines: [
          line({
            open_qty: '135',
            components: [
              {
                kind: 'reserve',
                qty: '62',
                reason: 'The shared pool at BRW covers this much within its cap.',
                source_location: 'BRW',
                source_warehouse_id: WH_BRW,
              },
              { kind: 'buy', qty: '73', reason: 'Remaining uncovered need.' },
            ],
            pool_allowances: { [WH_BRW]: '62' },
            pools_net: '100',
          }),
        ],
      }),
    );

    renderSection();
    await screen.findByText('Line 1 · CB6633');

    expect(
      screen.queryByText(/a line is either met wholly from stock or wholly bought/),
    ).not.toBeInTheDocument();
    expect(confirmButton()).toBeEnabled();
  });

  it('still refuses an OWN-BIN reserve beside a Buy: only a stated pool allowance carves it out', async () => {
    // Same 62 + 73 split, but the line states no `pool_allowances` for WH_BRW (an own bin
    // keeps no dealer share back) - the whole-line rule still refuses the mix.
    getSupply.mockResolvedValue(
      proposal({
        lines: [
          line({
            open_qty: '135',
            components: [
              {
                kind: 'reserve',
                qty: '62',
                reason: 'Free stock at BRW-BB covers this much.',
                source_location: 'BRW-BB',
                source_warehouse_id: WH_BRW,
              },
              { kind: 'buy', qty: '73', reason: 'Remaining uncovered need.' },
            ],
            pool_allowances: {},
            pools_net: null,
          }),
        ],
      }),
    );

    renderSection();
    await screen.findByText('Line 1 · CB6633');

    expect(
      await screen.findAllByText(
        'Line 1, CB6633: a line is either met wholly from stock or wholly bought. ' +
          'This one mixes 62 from stock with a Buy of 73.',
      ),
    ).toHaveLength(2);
    expect(confirmButton()).toBeDisabled();
  });

  it('renders no UUID-looking id, though every line is addressed by one', async () => {
    getSupply.mockResolvedValue(CONFIRMED);

    const { container } = renderSection();
    await screen.findByText('Revision 2');

    expect(container.textContent).not.toMatch(/[0-9a-f]{8}-[0-9a-f]{4}-/i);
  });
});

// NUMBER-FIELDS round 4 (S3): bad input in a quantity is never read as 0 at Confirm.
import { typeBadInput } from '@/test-utils/badInput';

describe('SupplyCompositionSection bad number input (S3)', () => {
  it('a Buy box holding bad input is not confirmed as 0: nothing is sent and the sentence shows on that box', async () => {
    renderSection();
    await screen.findByText('Line 1 · CB6633');
    typeBadInput(screen.getByLabelText('Buy on line 1'));
    const confirm = confirmButton();
    if (!(confirm as HTMLButtonElement).disabled) fireEvent.click(confirm);
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
    expect(confirmSupply).not.toHaveBeenCalled();
    expect(screen.getByText(/Enter a number/)).toBeInTheDocument();
  });
});
