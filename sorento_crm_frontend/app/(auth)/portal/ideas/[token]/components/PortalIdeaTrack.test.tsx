/**
 * IDEATION-IN-CRM Phase 2 red tests for the customer track page (AC-H-04, AC-H-05, AC-H-02 as
 * the page renders it). The service is the seam (a public portal token, no CRM session); the real
 * hook and component render.
 *
 * Contract: `PortalIdea` carries the idea text (`problem`, `proposedSolution`, `impact`,
 * `department`, ss `PublicIdeaStatusOut` names) so the page can show it.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

const toastError = vi.fn();
vi.mock('@/lib/toast', () => ({ toast: { success: vi.fn(), error: (...a: unknown[]) => toastError(...a), dismiss: vi.fn() } }));

const svc = vi.hoisted(() => {
  class PortalIdeaNotFoundError extends Error {
    constructor() {
      super('This link is not valid.');
      this.name = 'PortalIdeaNotFoundError';
    }
  }
  return {
    PortalIdeaNotFoundError,
    getPortalIdea: vi.fn(),
    listPortalComments: vi.fn(),
    postPortalComment: vi.fn(),
  };
});
vi.mock('@/services/portalIdeasService', () => svc);

import { formatDate } from '@/lib/helpers';
import { PortalIdeaTrack } from './PortalIdeaTrack';

const TOKEN = 'Ab3dEf9hJk2LmN0p';

const IDEA = {
  title: 'Faster quotes',
  ideaNumber: 'IDEA-0042',
  statusLabel: 'In review',
  statusColor: 'info',
  submittedAt: '2026-09-30T08:15:00Z',
  submitterFirstName: 'Jane',
  upvotes: 7,
  problem: 'Quotes take too long to write',
  proposedSolution: 'A template library',
  impact: null,
  department: null,
};

function comment(over: Record<string, unknown> = {}) {
  return {
    id: 'pc-1',
    parentId: null,
    authorName: 'Alex',
    isSubmitter: false,
    body: 'Thanks, we are looking at it.',
    createdAt: '2026-10-01T09:00:00Z',
    deleted: false,
    ...over,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  svc.getPortalIdea.mockResolvedValue(IDEA);
  svc.listPortalComments.mockResolvedValue([comment()]);
});

describe('AC-H-04 what the page shows', () => {
  it('shows the title, status badge, idea number, submitted date, vote count and the idea text', async () => {
    render(<PortalIdeaTrack token={TOKEN} />);
    expect(await screen.findByRole('heading', { name: 'Faster quotes' })).toBeInTheDocument();
    expect(screen.getByText('In review')).toBeInTheDocument();
    expect(screen.getByText('IDEA-0042')).toBeInTheDocument();
    expect(screen.getByText(new RegExp(formatDate('2026-09-30T08:15:00Z').replace(/\//g, '\\/')))).toBeInTheDocument();
    expect(screen.getByLabelText(/7 votes/)).toBeInTheDocument();
    expect(screen.getByText('Quotes take too long to write')).toBeInTheDocument();
    expect(screen.getByText('A template library')).toBeInTheDocument();
  });

  it('the vote count is READ-ONLY: no vote control anywhere on the page', async () => {
    render(<PortalIdeaTrack token={TOKEN} />);
    await screen.findByRole('heading', { name: 'Faster quotes' });
    expect(screen.queryByRole('button', { name: /vote/i })).toBeNull();
    const buttons = screen.getAllByRole('button').map((b) => b.textContent);
    expect(buttons).toEqual(['Comment']);
  });

  it('shows the thread with the commenter name', async () => {
    render(<PortalIdeaTrack token={TOKEN} />);
    expect(await screen.findByText('Thanks, we are looking at it.')).toBeInTheDocument();
    expect(screen.getByText('Alex')).toBeInTheDocument();
  });

  it('renders comment bodies as plain text', async () => {
    const hostile = '<img src=x onerror=alert(1)>hi';
    svc.listPortalComments.mockResolvedValue([comment({ body: hostile })]);
    const { container } = render(<PortalIdeaTrack token={TOKEN} />);
    expect(await screen.findByText(hostile)).toBeInTheDocument();
    expect(container.querySelector('img')).toBeNull();
  });
});

describe('heading fallback', () => {
  it('an idea with no title falls back to its problem statement as the page heading', async () => {
    svc.getPortalIdea.mockResolvedValue({ ...IDEA, title: null });
    render(<PortalIdeaTrack token={TOKEN} />);
    expect(await screen.findByRole('heading', { name: 'Quotes take too long to write' })).toBeInTheDocument();
  });
});

describe('AC-H-05 posting', () => {
  it('posts the body only and tags the just-posted comment "You"', async () => {
    svc.postPortalComment.mockResolvedValue(
      comment({ id: 'pc-9', authorName: 'Jane', isSubmitter: true, body: 'It would help us a lot.' }),
    );
    render(<PortalIdeaTrack token={TOKEN} />);
    await screen.findByRole('heading', { name: 'Faster quotes' });
    expect(screen.queryByText('You')).toBeNull();
    fireEvent.change(screen.getByLabelText('Write a comment'), { target: { value: 'It would help us a lot.' } });
    fireEvent.click(screen.getByRole('button', { name: 'Comment' }));
    await waitFor(() => expect(svc.postPortalComment).toHaveBeenCalledWith(TOKEN, 'It would help us a lot.'));
    expect(await screen.findByText('It would help us a lot.')).toBeInTheDocument();
    expect(screen.getByText('You')).toBeInTheDocument();
    expect(screen.getByText('Jane')).toBeInTheDocument();
  });

  it('refuses an empty comment before calling the service', async () => {
    render(<PortalIdeaTrack token={TOKEN} />);
    await screen.findByRole('heading', { name: 'Faster quotes' });
    const submit = screen.getByRole('button', { name: 'Comment' });
    expect(submit).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Write a comment'), { target: { value: '   ' } });
    expect(submit).toBeDisabled();
    expect(svc.postPortalComment).not.toHaveBeenCalled();
  });

  it('a rate-limit refusal toasts the server message and keeps what was typed', async () => {
    svc.postPortalComment.mockRejectedValue(new Error('Too many comments. Try again later.'));
    render(<PortalIdeaTrack token={TOKEN} />);
    await screen.findByRole('heading', { name: 'Faster quotes' });
    const box = screen.getByLabelText('Write a comment');
    fireEvent.change(box, { target: { value: 'One more thing' } });
    fireEvent.click(screen.getByRole('button', { name: 'Comment' }));
    await waitFor(() => expect(toastError).toHaveBeenCalledWith('Too many comments. Try again later.'));
    expect(box).toHaveValue('One more thing');
  });
});

describe('AC-H-02 / AC-H-08 states', () => {
  it('an unknown token renders "Idea not found" / "This link is not valid." and no thread', async () => {
    svc.getPortalIdea.mockRejectedValue(new svc.PortalIdeaNotFoundError());
    svc.listPortalComments.mockRejectedValue(new svc.PortalIdeaNotFoundError());
    render(<PortalIdeaTrack token="nope" />);
    expect(await screen.findByText('Idea not found')).toBeInTheDocument();
    expect(screen.getByText('This link is not valid.')).toBeInTheDocument();
    expect(screen.queryByLabelText('Write a comment')).toBeNull();
  });

  it('another failure is a load error, not "not found"', async () => {
    svc.getPortalIdea.mockRejectedValue(new Error('Server error. Try again or contact support.'));
    render(<PortalIdeaTrack token={TOKEN} />);
    expect(await screen.findByText('Could not load this idea')).toBeInTheDocument();
    expect(screen.queryByText('Idea not found')).toBeNull();
  });

  it('loading: a skeleton, not the form', () => {
    svc.getPortalIdea.mockReturnValue(new Promise(() => undefined));
    svc.listPortalComments.mockReturnValue(new Promise(() => undefined));
    render(<PortalIdeaTrack token={TOKEN} />);
    expect(screen.queryByLabelText('Write a comment')).toBeNull();
  });

  it('empty thread: heading and hint', async () => {
    svc.listPortalComments.mockResolvedValue([]);
    render(<PortalIdeaTrack token={TOKEN} />);
    expect(await screen.findByText('No comments yet')).toBeInTheDocument();
  });

  it('never prints a UUID-looking id', async () => {
    render(<PortalIdeaTrack token={TOKEN} />);
    await screen.findByRole('heading', { name: 'Faster quotes' });
    expect(document.body.textContent).not.toMatch(/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i);
  });
});
