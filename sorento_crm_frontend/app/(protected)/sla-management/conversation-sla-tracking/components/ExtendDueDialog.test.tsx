import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import ExtendDueDialog from './ExtendDueDialog';
import type { ExtendSLAPreview } from '../services/conversationSLATrackingService';

const getExtendPreview = vi.fn();
const extendSLATracking = vi.fn();

vi.mock('../services/conversationSLATrackingService', () => ({
  getExtendPreview: (...a: unknown[]) => getExtendPreview(...a),
  extendSLATracking: (...a: unknown[]) => extendSLATracking(...a),
}));

vi.mock('@/lib/toast', () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }));

// Keep date formatting deterministic + simple for assertions.
vi.mock('@/lib/helpers', () => ({
  formatDateTime: (d: Date) => `FMT:${d.toISOString()}`,
  parseDateTimeAsUTC: (s: string) => new Date(s.endsWith('Z') ? s : `${s}Z`),
}));

const CURRENT_DUE = '2026-07-01T09:00:00';

const preview3Days: ExtendSLAPreview = {
  current_due_at: CURRENT_DUE,
  new_due_at: '2026-07-06T09:00:00',
  working_days: 3,
  warnings: [],
};

function renderDialog(props: Partial<React.ComponentProps<typeof ExtendDueDialog>> = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const onOpenChange = vi.fn();
  const onExtended = vi.fn();
  const utils = render(
    <QueryClientProvider client={client}>
      <ExtendDueDialog
        open
        onOpenChange={onOpenChange}
        trackingId="t1"
        currentDueAt={CURRENT_DUE}
        label="Complaint · CMP-0001"
        onExtended={onExtended}
        {...props}
      />
    </QueryClientProvider>,
  );
  return { ...utils, onOpenChange, onExtended };
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.useRealTimers();
});

describe('ExtendDueDialog', () => {
  // UAC-8 (FE): days mode triggers a debounced preview and renders the new due + "+N working days".
  it('days mode: debounced preview renders new due and +N working days', async () => {
    getExtendPreview.mockResolvedValue(preview3Days);
    renderDialog();

    // Default days = "1" triggers an initial preview; set to 3.
    const daysInput = screen.getByLabelText(/additional working days/i);
    fireEvent.change(daysInput, { target: { value: '3' } });

    await waitFor(() => {
      expect(getExtendPreview).toHaveBeenCalledWith('t1', { days: 3 });
    });
    await waitFor(() => {
      expect(screen.getByText(/New due:/i)).toBeInTheDocument();
      expect(screen.getByText(/\+3 working days/i)).toBeInTheDocument();
    });
  });

  // UAC-11 (FE): date mode triggers a preview with target_date and shows the derived count.
  it('date mode: picking a date previews with target_date and shows derived working-day count', async () => {
    getExtendPreview.mockResolvedValue(preview3Days);
    renderDialog();

    fireEvent.click(screen.getByRole('button', { name: /specific date/i }));
    const dateInput = screen.getByLabelText(/new due date/i);
    fireEvent.change(dateInput, { target: { value: '2026-07-06' } });

    await waitFor(() => {
      expect(getExtendPreview).toHaveBeenCalledWith('t1', { target_date: '2026-07-06' });
    });
    await waitFor(() => {
      expect(screen.getByText(/\+3 working days/i)).toBeInTheDocument();
    });
  });

  // UAC-9 (FE): 0 / blank days -> no preview; Extend stays live and its click names the problem.
  it('blank/zero days does not call preview; Extend says what is wrong and sends nothing', async () => {
    getExtendPreview.mockResolvedValue(preview3Days);
    renderDialog();

    const daysInput = screen.getByLabelText(/additional working days/i);
    fireEvent.change(daysInput, { target: { value: '0' } });
    fireEvent.change(screen.getByLabelText(/reason/i), { target: { value: 'valid reason' } });

    // give the debounce a chance - it must NOT fire for invalid input
    await new Promise((r) => setTimeout(r, 500));
    expect(getExtendPreview).not.toHaveBeenCalled();
    expect(daysInput).toHaveValue(0);
    fireEvent.click(screen.getByRole('button', { name: /extend deadline/i }));
    expect(screen.getByText('Enter a whole number of at least 1.')).toBeInTheDocument();
    expect(extendSLATracking).not.toHaveBeenCalled();

    // blank: stays blank, same sentence
    fireEvent.change(daysInput, { target: { value: '' } });
    await new Promise((r) => setTimeout(r, 500));
    expect(getExtendPreview).not.toHaveBeenCalled();
    expect(daysInput).toHaveValue(null);
    fireEvent.click(screen.getByRole('button', { name: /extend deadline/i }));
    expect(screen.getByText('Enter a whole number of at least 1.')).toBeInTheDocument();
    expect(extendSLATracking).not.toHaveBeenCalled();
  });

  // UAC-17 (FE): empty reason -> submit disabled even with a valid preview.
  it('empty reason disables submit even with a valid preview', async () => {
    getExtendPreview.mockResolvedValue(preview3Days);
    renderDialog();

    fireEvent.change(screen.getByLabelText(/additional working days/i), {
      target: { value: '3' },
    });
    await waitFor(() => expect(screen.getByText(/New due:/i)).toBeInTheDocument());

    // reason still empty -> disabled
    expect(screen.getByRole('button', { name: /extend deadline/i })).toBeDisabled();

    // fill reason -> enabled
    fireEvent.change(screen.getByLabelText(/reason/i), { target: { value: 'supplier delay' } });
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /extend deadline/i })).toBeEnabled(),
    );
  });

  // UAC-13 / UAC-16 (FE): warnings render an amber banner AND submit stays enabled.
  it('renders soft-limit warnings without blocking submit', async () => {
    getExtendPreview.mockResolvedValue({
      ...preview3Days,
      warnings: ['This extension adds 3 working day(s), exceeding the per-request limit of 1.'],
    });
    renderDialog();

    fireEvent.change(screen.getByLabelText(/additional working days/i), {
      target: { value: '3' },
    });
    fireEvent.change(screen.getByLabelText(/reason/i), { target: { value: 'over the cap' } });

    await waitFor(() =>
      expect(screen.getByText(/exceeding the per-request limit/i)).toBeInTheDocument(),
    );
    // Single-click submit: still enabled despite the warning (UAC-16).
    expect(screen.getByRole('button', { name: /extend deadline/i })).toBeEnabled();
  });

  // UAC-19 (FE): a successful submit posts {days, reason} and fires onExtended.
  it('submit posts the input + reason and fires onExtended on success', async () => {
    getExtendPreview.mockResolvedValue(preview3Days);
    extendSLATracking.mockResolvedValue({ id: 't1' });
    const { onExtended, onOpenChange } = renderDialog();

    fireEvent.change(screen.getByLabelText(/additional working days/i), {
      target: { value: '3' },
    });
    fireEvent.change(screen.getByLabelText(/reason/i), { target: { value: 'supplier delay' } });

    await waitFor(() =>
      expect(screen.getByRole('button', { name: /extend deadline/i })).toBeEnabled(),
    );
    fireEvent.click(screen.getByRole('button', { name: /extend deadline/i }));

    await waitFor(() => {
      expect(extendSLATracking).toHaveBeenCalledWith('t1', { days: 3, reason: 'supplier delay' });
    });
    await waitFor(() => {
      expect(onExtended).toHaveBeenCalled();
      expect(onOpenChange).toHaveBeenCalledWith(false);
    });
  });
});
