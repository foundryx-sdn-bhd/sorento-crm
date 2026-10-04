import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render } from '@testing-library/react';

// Capture the onError/onSuccess callbacks that QueryProvider passes to QueryCache.
type CapturedQuery = {
  meta?: Record<string, unknown>;
  queryKey: readonly unknown[];
  queryHash: string;
};
let capturedOnError: ((error: Error, query: CapturedQuery) => void) | null = null;
let capturedOnSuccess: ((data: unknown, query: CapturedQuery) => void) | null = null;

// Capture the options QueryProvider constructs its ONE QueryClient with
// (M4-04): defaultOptions is the shared substitute for 176 per-hook repeats.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
let capturedQueryClientOptions: any = null;

vi.mock('@tanstack/react-query', () => {
  const actual = vi.importActual('@tanstack/react-query');
  return {
    ...actual,
    QueryCache: class {
      constructor(opts?: {
        onError?: typeof capturedOnError;
        onSuccess?: typeof capturedOnSuccess;
      }) {
        capturedOnError = opts?.onError ?? null;
        capturedOnSuccess = opts?.onSuccess ?? null;
      }
    },
    QueryClient: class {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      constructor(opts?: any) {
        capturedQueryClientOptions = opts;
      }
      invalidateQueries() {}
    },
    QueryClientProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  };
});

const toastCustom = vi.fn();
vi.mock('@/lib/toast', () => ({
  toast: { custom: (...args: unknown[]) => toastCustom(...args) },
}));

vi.mock('@/components/ui/alert', () => ({
  Alert: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  AlertIcon: ({ children }: { children: React.ReactNode }) => <span>{children}</span>,
  AlertTitle: ({ children }: { children: React.ReactNode }) => <span>{children}</span>,
}));

vi.mock('@remixicon/react', () => ({
  RiErrorWarningFill: () => <span>icon</span>,
}));

vi.mock('@/lib/revision-fence', () => ({
  registerRevisionStaleHandler: vi.fn(),
}));

// SESSION-NEVER-STUCK: the provider reads the session-ending latch and registers the
// view-as-ended refetch; both are driven from here.
const sessionEnd = vi.hoisted(() => ({
  ending: false,
  viewAsEnded: null as null | (() => void),
}));
vi.mock('@/lib/session-end', () => ({
  isSessionEnding: () => sessionEnd.ending,
  registerViewAsEndedHandler: (fn: (() => void) | null) => {
    sessionEnd.viewAsEnded = fn;
  },
}));

vi.mock('@/services/pendingActionService', () => ({
  createPendingAction: vi.fn(),
  cancelPendingAction: vi.fn(),
  getCurrentPendingAction: vi.fn(),
}));

import fs from 'node:fs';
import path from 'node:path';
import { QueryProvider } from './query-provider';
import { pendingEntityStore } from '@/lib/pending-entity-store';

/** Every non-test `.ts`/`.tsx` file under `dir`. */
function listSourceFiles(dir: string, out: string[] = []): string[] {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name === 'node_modules' || entry.name === '.next') continue;
      listSourceFiles(full, out);
    } else if (/\.(ts|tsx)$/.test(entry.name) && !/\.(test|spec)\./.test(entry.name)) {
      out.push(full);
    }
  }
  return out;
}

function fireOnError(
  message: string,
  meta?: Record<string, unknown>,
  queryKey: readonly unknown[] = ['something'],
  queryHash: string = JSON.stringify(queryKey),
) {
  if (!capturedOnError) throw new Error('onError not captured - render QueryProvider first');
  capturedOnError(new Error(message), { meta, queryKey, queryHash });
}

function fireOnSuccess(
  queryKey: readonly unknown[] = ['something'],
  queryHash: string = JSON.stringify(queryKey),
) {
  if (!capturedOnSuccess) throw new Error('onSuccess not captured - render QueryProvider first');
  capturedOnSuccess({}, { queryKey, queryHash });
}

describe('QueryProvider toast deduplication', () => {
  beforeEach(() => {
    capturedOnError = null;
    capturedOnSuccess = null;
    toastCustom.mockClear();
    pendingEntityStore.reset();
    render(
      <QueryProvider>
        <div />
      </QueryProvider>,
    );
  });

  it('collapses three concurrent permission-denied 403s (three different queries) into one visible toast', () => {
    fireOnError('Permission required: scm.dashboard.view', undefined, ['dashboard']);
    fireOnError('Permission required: scm.reorder.run', undefined, ['reorder']);
    fireOnError('Permission required: scm.policy.manage', undefined, ['policy']);

    // All three should produce calls, but they share the same id so sonner
    // deduplicates them into one visible toast. We verify the id is set.
    expect(toastCustom).toHaveBeenCalledTimes(3);
    for (const call of toastCustom.mock.calls) {
      expect(call[1]).toEqual(
        expect.objectContaining({ id: 'permission-denied', duration: 5000 }),
      );
    }
  });

  it('toasts "One of these permissions required:" with the same deduped id', () => {
    fireOnError('One of these permissions required: a.b, c.d');

    expect(toastCustom).toHaveBeenCalledTimes(1);
    expect(toastCustom.mock.calls[0][1]).toEqual(
      expect.objectContaining({ id: 'permission-denied', duration: 5000 }),
    );
  });

  it('passes a 500 error through, deduped by message, auto-dismissing at 5000ms', () => {
    fireOnError('Internal server error');

    expect(toastCustom).toHaveBeenCalledTimes(1);
    const opts = toastCustom.mock.calls[0][1];
    expect(opts).toEqual(
      expect.objectContaining({ id: 'query-error:Internal server error', duration: 5000 }),
    );
  });

  it('keeps permission toast and regular toast separate', () => {
    fireOnError('Permission required: scm.dashboard.view', undefined, ['dashboard']);
    fireOnError('Internal server error', undefined, ['orders']);

    expect(toastCustom).toHaveBeenCalledTimes(2);
    expect(toastCustom.mock.calls[0][1]).toEqual(
      expect.objectContaining({ id: 'permission-denied', duration: 5000 }),
    );
    expect(toastCustom.mock.calls[1][1]).toEqual(
      expect.objectContaining({ id: 'query-error:Internal server error', duration: 5000 }),
    );
  });

  it('suppresses toast when query meta has silent: true', () => {
    fireOnError('Permission required: scm.dashboard.view', { silent: true });

    expect(toastCustom).not.toHaveBeenCalled();
  });

  // The owner complaint this fixes: a query that keeps failing on every
  // background refetch (window focus, polling, an invalidation) used to
  // re-raise the same toast every cycle, which read as one toast that never
  // left the screen.
  it('toasts the first failure of a query once, with a 5000ms duration', () => {
    fireOnError('Stock levels unavailable', undefined, ['stock', 'sku-1']);

    expect(toastCustom).toHaveBeenCalledTimes(1);
    expect(toastCustom.mock.calls[0][1]).toEqual(
      expect.objectContaining({ duration: 5000 }),
    );
  });

  it('does not re-toast a second failure of the same query', () => {
    fireOnError('Stock levels unavailable', undefined, ['stock', 'sku-1']);
    fireOnError('Stock levels unavailable', undefined, ['stock', 'sku-1']);
    fireOnError('Stock levels unavailable', undefined, ['stock', 'sku-1']);

    expect(toastCustom).toHaveBeenCalledTimes(1);
  });

  it('toasts again once the query succeeds and then fails again', () => {
    fireOnError('Stock levels unavailable', undefined, ['stock', 'sku-1']);
    expect(toastCustom).toHaveBeenCalledTimes(1);

    fireOnSuccess(['stock', 'sku-1']);
    fireOnError('Stock levels unavailable', undefined, ['stock', 'sku-1']);

    expect(toastCustom).toHaveBeenCalledTimes(2);
  });

  // S6 feedback C: a record the user watched a delete commit on is gone on
  // purpose. The detail read, its tabs and its counts all 404 at once, and a
  // stack of red toasts for the thing they asked for is not an answer.
  it('says nothing about reads keyed on a record this tab just deleted', () => {
    pendingEntityStore.noteCommittedDelete('p-1');

    fireOnError('Product not found', undefined, ['product', 'p-1']);
    fireOnError('Product not found', undefined, ['product-attachments', 'p-1']);

    expect(toastCustom).not.toHaveBeenCalled();

    // Everything else still reports normally.
    fireOnError('Internal server error', undefined, ['product', 'p-2']);
    expect(toastCustom).toHaveBeenCalledTimes(1);
  });
});

// M4-04: the ONE place freshness is configured, so 176 per-hook repeats do
// not have to agree with each other by hand.
describe('QueryProvider default query options (M4-04)', () => {
  it('sets retry 1, staleTime 30s and no refetch on window focus', () => {
    render(
      <QueryProvider>
        <div />
      </QueryProvider>,
    );

    expect(capturedQueryClientOptions.defaultOptions.queries).toEqual({
      retry: 1,
      staleTime: 30_000,
      refetchOnWindowFocus: false,
    });
  });

  // The 176 per-hook repeats of this exact value are gone: the default above
  // is now the ONLY place it is set. A hook opting into `true` (a genuinely
  // different value) is untouched by this test.
  it('nothing outside this provider sets refetchOnWindowFocus: false itself', () => {
    // Every directory a query can be written in, not just `app/` and `hooks/`:
    // the first pass scanned two of them and left the repeat in
    // `components/common/LinkAttachmentBrowserDialog.tsx` standing.
    const root = path.resolve(__dirname, '..');
    const files = ['app', 'components', 'hooks', 'lib', 'providers', 'services'].flatMap((dir) =>
      listSourceFiles(path.join(root, dir)),
    );
    const self = path.join(root, 'providers', 'query-provider.tsx');

    const offenders = files
      .filter((file) => file !== self)
      .filter((file) => fs.readFileSync(file, 'utf8').includes('refetchOnWindowFocus: false'))
      .map((file) => path.relative(root, file));

    expect(offenders).toEqual([]);
  });
});

describe('QueryProvider while a dead session is ending (SESSION-NEVER-STUCK)', () => {
  beforeEach(() => {
    capturedOnError = null;
    toastCustom.mockClear();
    sessionEnd.ending = false;
    sessionEnd.viewAsEnded = null;
    render(
      <QueryProvider>
        <div />
      </QueryProvider>,
    );
  });

  it('raises no error toast once the session is on its way to sign-in', () => {
    sessionEnd.ending = true;
    fireOnError('Your session has ended. Please sign in again.', undefined, ['a']);
    fireOnError('Failed to fetch my pending SLAs', undefined, ['b']);

    expect(toastCustom).not.toHaveBeenCalled();
  });

  it('still toasts an ordinary failure while the session is alive', () => {
    fireOnError('Failed to fetch my pending SLAs', undefined, ['c']);

    expect(toastCustom).toHaveBeenCalledTimes(1);
  });

  it('an ended view-as refetches every query (cached data may be the target\'s)', async () => {
    const { QueryClient } = await import('@tanstack/react-query');
    const invalidate = vi.spyOn(QueryClient.prototype, 'invalidateQueries');

    expect(sessionEnd.viewAsEnded).toBeTypeOf('function');
    sessionEnd.viewAsEnded!();

    expect(invalidate).toHaveBeenCalledTimes(1);
    expect(invalidate).toHaveBeenCalledWith();
    invalidate.mockRestore();
  });
});
