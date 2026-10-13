/**
 * CLAUDE-CONNECTOR r3 red test: SS redirects the browser to /connect/claude?req=<consent_request JWT>
 * (contract section 2). The page hands `req` to <ConsentCard req/>; without it, the expired view.
 */
import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';

let params = new URLSearchParams();
vi.mock('next/navigation', () => ({ useSearchParams: () => params }));
vi.mock('./components/ConsentCard', () => ({
  default: (props: Record<string, unknown>) => <div data-testid="card">{JSON.stringify(props)}</div>,
}));

import ConnectClaudePage from './page';

afterEach(cleanup);

describe('/connect/claude', () => {
  it('passes ?req= to the consent card', async () => {
    params = new URLSearchParams({ req: 'eyJ.zzt.req' });
    render(<ConnectClaudePage />);
    expect(JSON.parse((await screen.findByTestId('card')).textContent || '{}')).toEqual({ req: 'eyJ.zzt.req' });
  });

  it('ignores the r2 ?request= parameter', async () => {
    params = new URLSearchParams({ request: 'old-id' });
    render(<ConnectClaudePage />);
    expect(await screen.findByText(/return to claude and connect again/i)).toBeInTheDocument();
    expect(screen.queryByTestId('card')).not.toBeInTheDocument();
  });
});
