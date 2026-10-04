/**
 * Internal-note composer with @mention typeahead (UAC AC-L1).
 *
 * The user-select service is mocked: the rule is that mentions resolve through
 * the ONE shared `userSelectService` (ARCHITECTURE-RULES), so the test asserts
 * that call rather than a per-feature fetch.
 */
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import InternalCommentComposer, { activeMentionFragment } from './InternalCommentComposer';

const getUserLookup = vi.fn();

vi.mock('@/services/userSelectService', () => ({
  getUserLookup: (...a: unknown[]) => getUserLookup(...a),
}));

function renderComposer(props: Partial<React.ComponentProps<typeof InternalCommentComposer>> = {}) {
  const onSubmit = vi.fn().mockResolvedValue(undefined);
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  render(
    <QueryClientProvider client={client}>
      <InternalCommentComposer onSubmit={onSubmit} {...props} />
    </QueryClientProvider>,
  );
  return { onSubmit };
}

function type(value: string) {
  const input = screen.getByTestId('internal-comment-input') as HTMLTextAreaElement;
  fireEvent.change(input, { target: { value } });
  // jsdom leaves selectionStart at 0 after a programmatic change; the component
  // reads it to find the "@" fragment, so put the caret where a typist's is.
  input.selectionStart = value.length;
  input.selectionEnd = value.length;
  return input;
}

beforeEach(() => {
  vi.useRealTimers();
  getUserLookup.mockReset();
  getUserLookup.mockResolvedValue([
    { id: 'u-1', name: 'Team Lead' },
    { id: 'u-2', name: 'Warehouse Sam' },
  ]);
});

describe('activeMentionFragment', () => {
  it('finds the fragment being typed at the caret', () => {
    expect(activeMentionFragment('hello @Te', 9)).toEqual({ start: 6, query: 'Te' });
  });

  it('allows spaces so real names can be typed', () => {
    expect(activeMentionFragment('@Team Le', 8)).toEqual({ start: 0, query: 'Team Le' });
  });

  it('ignores an @ that is part of a word, so an email is never a mention', () => {
    expect(activeMentionFragment('mail sam@test.com', 17)).toBeNull();
  });

  it('closes at a newline rather than swallowing the next line', () => {
    expect(activeMentionFragment('@Team\nnext', 10)).toBeNull();
  });

  it('is null with no @ at all', () => {
    expect(activeMentionFragment('just a note', 11)).toBeNull();
  });
});

describe('InternalCommentComposer', () => {
  it('empty state: the note cannot be added until something is typed', () => {
    renderComposer();
    expect(screen.getByRole('button', { name: /Add note/i })).toBeDisabled();
  });

  it('is visually marked as internal so it is never mistaken for a reply', () => {
    renderComposer();
    expect(screen.getByText(/Internal note/i)).toBeInTheDocument();
  });

  it('disabled state: renders the reason and no input at all', () => {
    renderComposer({ disabled: true, disabledMessage: 'This ticket is resolved.' });
    expect(screen.getByText('This ticket is resolved.')).toBeInTheDocument();
    expect(screen.queryByTestId('internal-comment-input')).not.toBeInTheDocument();
  });

  it('more matches than the window shows draw a keep-typing footer', async () => {
    (getUserLookup as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      Array.from({ length: 12 }, (_, i) => ({
        id: `u${i}`,
        name: `User ${String.fromCharCode(65 + i)}`,
      })),
    );
    renderComposer();
    type('@');
    const more = await screen.findByTestId('mention-typeahead-more');
    expect(more).toHaveTextContent('4 more. Keep typing to narrow.');
  });

  it('typing "@" opens the typeahead over the SHARED user select service', async () => {
    renderComposer();
    type('@Te');

    await waitFor(() => expect(screen.getByTestId('mention-typeahead')).toBeInTheDocument());
    await waitFor(() =>
      expect(getUserLookup).toHaveBeenCalledWith(expect.objectContaining({ query: '@Te'.slice(1) })),
    );
    expect(await screen.findByRole('option', { name: 'Team Lead' })).toBeInTheDocument();
  });

  it('picking a suggestion inserts "@Display Name" into the body', async () => {
    renderComposer();
    type('please check @Te');

    fireEvent.mouseDown(await screen.findByRole('option', { name: 'Team Lead' }));

    await waitFor(() =>
      expect(screen.getByTestId('internal-comment-input')).toHaveValue(
        'please check @Team Lead ',
      ),
    );
  });

  it('submits the body with the picked mention id', async () => {
    const { onSubmit } = renderComposer();
    type('@Te');
    fireEvent.mouseDown(await screen.findByRole('option', { name: 'Team Lead' }));
    await waitFor(() =>
      expect(screen.getByTestId('internal-comment-input')).toHaveValue('@Team Lead '),
    );

    fireEvent.click(screen.getByRole('button', { name: /Add note/i }));

    await waitFor(() =>
      expect(onSubmit).toHaveBeenCalledWith({ body: '@Team Lead', mentionedUserIds: ['u-1'] }),
    );
  });

  it('deleting the mention from the body drops it from the payload', async () => {
    const { onSubmit } = renderComposer();
    type('@Te');
    fireEvent.mouseDown(await screen.findByRole('option', { name: 'Team Lead' }));
    await waitFor(() =>
      expect(screen.getByTestId('internal-comment-input')).toHaveValue('@Team Lead '),
    );

    type('never mind');
    fireEvent.click(screen.getByRole('button', { name: /Add note/i }));

    await waitFor(() =>
      expect(onSubmit).toHaveBeenCalledWith({ body: 'never mind', mentionedUserIds: [] }),
    );
  });

  it('clears the draft after a successful add', async () => {
    renderComposer();
    type('all good');
    fireEvent.click(screen.getByRole('button', { name: /Add note/i }));

    await waitFor(() => expect(screen.getByTestId('internal-comment-input')).toHaveValue(''));
  });

  it('error state: a failed add keeps the draft and names the failure', async () => {
    const onSubmit = vi.fn().mockRejectedValue(new Error('Failed to add the note'));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <InternalCommentComposer onSubmit={onSubmit} />
      </QueryClientProvider>,
    );

    type('keep me');
    fireEvent.click(screen.getByRole('button', { name: /Add note/i }));

    expect(await screen.findByText('Failed to add the note')).toBeInTheDocument();
    expect(screen.getByTestId('internal-comment-input')).toHaveValue('keep me');
  });

  it('opens the typeahead upward, so a drawer at phone width does not clip it', async () => {
    // FINDING 10: it opened DOWNWARD (translate-y-full) from a composer that
    // sits at the bottom of the drawer, so the suggestions fell off the screen.
    renderComposer();
    type('@Te');

    const menu = await screen.findByTestId('mention-typeahead');
    expect(menu.className).toContain('bottom-full');
    expect(menu.className).not.toContain('translate-y-full');
  });

  it('error state: says the people lookup failed instead of vanishing', async () => {
    // FINDING 12: a failed lookup closed the typeahead with no explanation, so
    // "@" simply stopped working.
    getUserLookup.mockRejectedValue(new Error('Failed to load users'));
    renderComposer();
    type('@Te');

    expect(await screen.findByTestId('mention-typeahead-error')).toHaveTextContent(
      'Failed to load users',
    );
  });

  it('Escape closes the typeahead without touching the draft', async () => {
    renderComposer();
    const input = type('@Te');
    await screen.findByRole('option', { name: 'Team Lead' });

    fireEvent.keyDown(input, { key: 'Escape' });

    await waitFor(() => expect(screen.queryByTestId('mention-typeahead')).not.toBeInTheDocument());
    expect(input).toHaveValue('@Te');
  });
});
