import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { PromptDetail } from './PromptDetail';
import type {
  PromptKeySummary,
  PromptVersionDetail,
  PromptVersionsResponse,
} from '../../services/aiPromptsService';

const usePromptVersions = vi.fn();
const usePromptKeys = vi.fn();
const usePromptVersion = vi.fn();
const useSaveVersion = vi.fn();
const useSetLabel = vi.fn();
const useDryRun = vi.fn();
const useHasPermission = vi.fn();
const useRegistryVariables = vi.fn();

vi.mock('../../hooks/useAIAssistantPrompts', () => ({
  usePromptVersions: () => usePromptVersions(),
  usePromptVersion: () => usePromptVersion(),
  useSaveVersion: () => useSaveVersion(),
  useSetLabel: () => useSetLabel(),
  useDryRun: () => useDryRun(),
  // The key list is read twice on this screen: by the "Runs on" card for the agent's
  // model, and here for `dry_runnable`, which is a property of the KEY and is absent
  // from the versions response.
  usePromptKeys: () => usePromptKeys(),
  useSetAgentModel: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useRegistryVariables: () => useRegistryVariables(),
}));
vi.mock('@/hooks/usePermissions', () => ({
  useHasPermission: () => useHasPermission(),
}));
// Container pulls SettingsProvider context we don't need in a unit test.
vi.mock('@/components/common/container', () => ({
  Container: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

const META: PromptVersionsResponse = {
  name: 'router',
  role: 'Intent / routing',
  active: true,
  activates_in: null,
  variables: ['current_date'],
  labels: { production: 2, staging: null },
  versions: [
    { id: 'router-2', version: 2, commit_message: 'v2', created_by_name: 'Jay', created_at: '2026-07-03T09:00:00', labels: ['production'] },
    { id: 'router-1', version: 1, commit_message: 'seed', created_by_name: 'Seed', created_at: '2026-07-02T09:00:00', labels: [] },
  ],
};

const BASE: PromptVersionDetail = {
  id: 'router-2',
  name: 'router',
  version: 2,
  template: 'Classify {{current_date}}.',
  variables: ['current_date'],
  commit_message: 'v2',
  created_by_name: 'Jay',
  created_at: '2026-07-03T09:00:00',
  labels: ['production'],
};

/** The key list row for `router`, as the prompts list serves it. */
const KEY_ROW: PromptKeySummary = {
  name: 'router',
  role: 'Intent / routing',
  active: true,
  activates_in: null,
  variables: ['current_date'],
  dry_runnable: true,
  production_version: 2,
  staging_version: null,
  latest_version: 2,
  updated_at: '2026-07-03T09:00:00',
  updated_by_name: 'Jay',
  provider: null,
  model: null,
};

const saveMutate = vi.fn();
const labelMutate = vi.fn();

function renderDetail() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <PromptDetail name="router" />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  [
    usePromptVersions,
    usePromptVersion,
    usePromptKeys,
    useSaveVersion,
    useSetLabel,
    useDryRun,
    useHasPermission,
  ].forEach((m) => m.mockReset());
  saveMutate.mockReset();
  labelMutate.mockReset();
  useHasPermission.mockReturnValue(true);
  usePromptVersions.mockReturnValue({ data: META, isLoading: false, isError: false });
  usePromptKeys.mockReturnValue({ data: [KEY_ROW], isLoading: false, isError: false });
  usePromptVersion.mockReturnValue({ data: BASE, isLoading: false, isError: false });
  useSaveVersion.mockReturnValue({ mutate: saveMutate, isPending: false });
  useSetLabel.mockReturnValue({ mutate: labelMutate, isPending: false });
  useDryRun.mockReturnValue({ mutate: vi.fn(), isPending: false, data: undefined, isError: false });
  useRegistryVariables.mockReset().mockReturnValue({ data: [], isLoading: false, isError: false });
});
afterEach(() => cleanup());

describe('PromptDetail', () => {
  it('loads the production version into the editor by default', () => {
    renderDetail();
    const editor = screen.getByTestId('prompt-editor') as HTMLTextAreaElement;
    expect(editor.value).toBe('Classify {{current_date}}.');
  });

  it('shows a green chip for a present declared var', () => {
    renderDetail();
    const chip = screen.getByTestId('var-chip-current_date');
    expect(chip.getAttribute('data-state')).toBe('present');
  });

  it('hard-blocks save when an unknown token is present', () => {
    renderDetail();
    const editor = screen.getByTestId('prompt-editor');
    fireEvent.change(editor, { target: { value: 'Classify {{current_date}} and {{bogus}}' } });
    fireEvent.change(screen.getByTestId('commit-message'), { target: { value: 'msg' } });
    expect(screen.getByTestId('var-unknown-error')).toBeInTheDocument();
    expect(screen.getByTestId('save-version')).toBeDisabled();
  });

  it('soft-warns (does not block) when a declared var is removed', () => {
    renderDetail();
    fireEvent.change(screen.getByTestId('prompt-editor'), { target: { value: 'Classify without the var.' } });
    fireEvent.change(screen.getByTestId('commit-message'), { target: { value: 'msg' } });
    expect(screen.getByTestId('var-missing-warning')).toBeInTheDocument();
    expect(screen.getByTestId('save-version')).not.toBeDisabled();
  });

  it('keeps save disabled until there is a commit message', () => {
    renderDetail();
    fireEvent.change(screen.getByTestId('prompt-editor'), { target: { value: 'Classify {{current_date}} edited.' } });
    expect(screen.getByTestId('save-version')).toBeDisabled();
    fireEvent.change(screen.getByTestId('commit-message'), { target: { value: 'msg' } });
    expect(screen.getByTestId('save-version')).not.toBeDisabled();
  });

  it('opens the publish confirm dialog for a non-production version', () => {
    renderDetail();
    fireEvent.click(screen.getByTestId('publish-production-1'));
    expect(screen.getByText('Publish router v1 to production?')).toBeInTheDocument();
    expect(screen.getByText(/changes the live assistant immediately/i)).toBeInTheDocument();
  });

  it('offers the dry-run for an active key that the assistant turn actually reads', () => {
    renderDetail();
    expect(screen.getByTestId('dry-run-input')).toBeInTheDocument();
    expect(screen.queryByTestId('dry-run-disabled')).not.toBeInTheDocument();
  });

  it('disables the dry-run for a key outside the assistant pipeline, and says why', () => {
    usePromptKeys.mockReturnValue({
      data: [{ ...KEY_ROW, dry_runnable: false }],
      isLoading: false,
      isError: false,
    });
    renderDetail();
    expect(screen.queryByTestId('dry-run-input')).not.toBeInTheDocument();
    expect(screen.getByTestId('dry-run-disabled')).toHaveTextContent(
      /not part of the assistant pipeline/i,
    );
  });

  it('keeps the dormant reason when a dormant key is also not dry-runnable', () => {
    usePromptVersions.mockReturnValue({
      data: { ...META, active: false, activates_in: 'M2.5' },
      isLoading: false,
      isError: false,
    });
    usePromptKeys.mockReturnValue({
      data: [{ ...KEY_ROW, active: false, dry_runnable: false }],
      isLoading: false,
      isError: false,
    });
    renderDetail();
    expect(screen.getByTestId('dry-run-disabled')).toHaveTextContent(/Dormant key/i);
  });

  it('leaves the dry-run offered while the key list is still loading', () => {
    usePromptKeys.mockReturnValue({ data: undefined, isLoading: true, isError: false });
    renderDetail();
    expect(screen.getByTestId('dry-run-input')).toBeInTheDocument();
  });

  it('shows an AlertDialog (not window.confirm) when switching versions with unsaved edits', () => {
    const confirmSpy = vi.spyOn(window, 'confirm');
    renderDetail();
    // make the editor dirty
    fireEvent.change(screen.getByTestId('prompt-editor'), { target: { value: 'Classify {{current_date}} edited.' } });
    // click the other version row to switch
    fireEvent.click(screen.getByTestId('version-row-1'));
    expect(confirmSpy).not.toHaveBeenCalled();
    expect(screen.getByText(/Discard unsaved edits\?/i)).toBeInTheDocument();
    confirmSpy.mockRestore();
  });

  // ---------------------------------------------------------------------
  // Slice E: a key with no saved version opened an EMPTY editor - the
  // `useEffect` that seeds `draft` only fired off `baseQuery.data.template`,
  // which never arrives when there is nothing to load a version FROM.
  // `fallback_text` (the code default `PROMPT_KEYS[name].fallback()`) is
  // what the editor seeds from instead.
  // ---------------------------------------------------------------------

  it('seeds the draft from fallback_text when the key has no saved version', () => {
    usePromptVersions.mockReturnValue({
      data: {
        ...META,
        labels: { production: null, staging: null },
        versions: [],
        fallback_text: 'RULE TEXT',
      },
      isLoading: false,
      isError: false,
    });
    usePromptVersion.mockReturnValue({ data: undefined, isLoading: false, isError: false });

    renderDetail();

    const editor = screen.getByTestId('prompt-editor') as HTMLTextAreaElement;
    expect(editor.value).toBe('RULE TEXT');
    // Browser re-check: `baseVersion` never resolves with no saved version,
    // so "(base: v{baseVersion})" would have printed "(base: vnull)".
    expect(screen.getByText(/\(base: code default\)/)).toBeInTheDocument();
  });

  it('loads the saved version template, not fallback_text, when a version exists', () => {
    usePromptVersions.mockReturnValue({
      data: { ...META, fallback_text: 'RULE TEXT (should not show)' },
      isLoading: false,
      isError: false,
    });
    // usePromptVersion already returns BASE (template 'Classify {{current_date}}.')
    // from the shared beforeEach.

    renderDetail();

    const editor = screen.getByTestId('prompt-editor') as HTMLTextAreaElement;
    expect(editor.value).toBe('Classify {{current_date}}.');
  });
});

describe('PromptDetail with registry variables (PROMPT-DYNAMIC R5a)', () => {
  const REG_META: PromptVersionsResponse = {
    ...META,
    name: 'chatbot_semantic_parser',
    registry_variables: ['domains', 'statuses'],
  };
  const REG_BASE: PromptVersionDetail = { ...BASE, template: 'ONE of: {{domains}} | null {{current_date}}' };
  const REG_ROWS = [
    { name: 'domains', label: 'Domains', source: 'Chatbot Domains', href: '/system-management/chatbot-domains', count: 3, last_changed: null, rendered: 'order | sales | inventory', used: true },
    { name: 'statuses', label: 'Status words', source: 'Chatbot Status Words', href: '/system-management/chatbot-status-words', count: 8, last_changed: null, rendered: 'S', used: false },
  ];

  beforeEach(() => {
    usePromptVersions.mockReturnValue({ data: REG_META, isLoading: false, isError: false });
    usePromptVersion.mockReturnValue({ data: REG_BASE, isLoading: false, isError: false });
    useRegistryVariables.mockReturnValue({ data: REG_ROWS, isLoading: false, isError: false });
  });

  it('edits in the chip editor, not the plain textarea', () => {
    renderDetail();
    expect(screen.queryByTestId('prompt-editor')).toBeNull();
    const chipEditor = screen.getByTestId('prompt-chip-editor');
    expect(chipEditor.querySelector('[data-chip="domains"]')).not.toBeNull();
  });

  it('shows the wired panel', () => {
    renderDetail();
    expect(screen.getByTestId('wired-row-domains')).toHaveTextContent('In wording');
    expect(screen.getByTestId('wired-row-statuses')).toHaveTextContent('not in wording');
  });

  it('registry tokens never block saving', () => {
    renderDetail();
    expect(screen.queryByTestId('var-unknown-error')).toBeNull();
  });

  it('Preview rendered prompt shows the text the model receives, read-only', () => {
    renderDetail();
    // Radix tabs activate on mousedown (the repo's own convention, e.g. SalesTeamDetail.test.tsx).
    fireEvent.mouseDown(screen.getByTestId('editor-mode-preview'), { button: 0 });
    const preview = screen.getByTestId('prompt-preview');
    expect(preview.textContent).toContain('ONE of: order | sales | inventory | null');
    expect(preview.textContent).not.toContain('{{domains}}');
    // The editor stays mounted but hidden, so the caret survives (hand test #1405, item 2).
    expect(screen.getByTestId('chip-editor-pane')).not.toBeVisible();
    fireEvent.mouseDown(screen.getByTestId('editor-mode-edit'), { button: 0 });
    expect(screen.getByTestId('chip-editor-pane')).toBeVisible();
  });
});

describe('PromptDetail editor mode uses the Tabs primitive (reviewer pass 2)', () => {
  it('Edit and Preview are tabs', () => {
    usePromptVersions.mockReturnValue({ data: { ...META, name: 'chatbot_semantic_parser', registry_variables: ['domains'] }, isLoading: false, isError: false });
    usePromptVersion.mockReturnValue({ data: { ...BASE, template: 'x {{domains}}' }, isLoading: false, isError: false });
    useRegistryVariables.mockReturnValue({ data: [], isLoading: false, isError: false });
    renderDetail();
    const edit = screen.getByTestId('editor-mode-edit');
    const preview = screen.getByTestId('editor-mode-preview');
    expect(edit.getAttribute('role')).toBe('tab');
    expect(edit.getAttribute('data-slot')).toBe('tabs-trigger');
    expect(preview.getAttribute('aria-selected')).toBe('false');
  });
});

describe('PromptDetail wired-panel insert (owner hand test #1405, item 2)', () => {
  it('inserts at the caret in the editor, not at the bottom', () => {
    usePromptVersions.mockReturnValue({ data: { ...META, name: 'chatbot_semantic_parser', registry_variables: ['domains', 'statuses'] }, isLoading: false, isError: false });
    usePromptVersion.mockReturnValue({ data: { ...BASE, template: 'domain_hint = ONE of: | null\nEND' }, isLoading: false, isError: false });
    useRegistryVariables.mockReturnValue({
      data: [{ name: 'domains', label: 'Domains', source: 'Chatbot Domains', href: '/x', count: 3, last_changed: null, rendered: 'a | b', used: false }],
      isLoading: false,
      isError: false,
    });
    renderDetail();
    const editor = screen.getByTestId('prompt-chip-editor');
    const textNode = editor.firstChild as Text;
    const range = document.createRange();
    range.setStart(textNode, 'domain_hint = ONE of:'.length);
    range.collapse(true);
    window.getSelection()!.removeAllRanges();
    window.getSelection()!.addRange(range);
    fireEvent.mouseUp(editor);
    window.getSelection()!.removeAllRanges();
    fireEvent.click(screen.getByTestId('wired-insert-domains'));
    const chip = editor.querySelector('[data-chip="domains"]')!;
    expect(chip).not.toBeNull();
    expect(chip.previousSibling!.textContent!.endsWith('domain_hint = ONE of:')).toBe(true);
    expect(editor.lastChild!.textContent!.endsWith('END')).toBe(true);
  });
});
