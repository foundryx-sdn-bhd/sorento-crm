'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import Link from 'next/link';
import { ArrowLeft, GitCompare, Loader2, Save } from 'lucide-react';
import { toast } from '@/lib/toast';
import { useHasPermission } from '@/hooks/usePermissions';
import { cn } from '@/lib/utils';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Container } from '@/components/common/container';
import { PageHeader } from '@/components/common/PageHeader';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { SectionSkeleton } from '@/components/common/SectionSkeleton';
import { SearchableTextarea } from '@/components/common/find-in-text/SearchableTextarea';
import {
  usePromptKeys,
  usePromptVersion,
  usePromptVersions,
  useRegistryVariables,
  useSaveVersion,
  useSetLabel,
} from '../../hooks/useAIAssistantPrompts';
import { validateVars } from '../../lib/promptVars';
import { splitTemplate } from '../../lib/promptSegments';
import { CHATBOT_TURN_PROMPT_KEYS } from '../../services/aiPromptsService';
import type { SaveVersionError } from '../../services/aiPromptsService';
import { AgentModelCard } from './AgentModelCard';
import { DiffView } from './DiffView';
import { DryRunBox } from './DryRunBox';
import { PromptChipEditor, type PromptChipEditorHandle } from './PromptChipEditor';
import { PublishDialog } from './PublishDialog';
import { VarChips } from './VarChips';
import { WiredPanel } from './WiredPanel';

const EDIT_PERMISSION = 'system.ai_assistant_settings.edit';

export function PromptDetail({ name }: { name: string }) {
  const versionsQuery = usePromptVersions(name);
  const canEdit = useHasPermission(EDIT_PERMISSION);

  const meta = versionsQuery.data;
  const productionVersion = meta?.labels.production ?? meta?.versions[0]?.version ?? null;

  // Whether a dry-run of THIS key would exercise the edit. It is a property of the
  // key rather than of a version, and the versions response does not carry it, so it
  // is read off the key list - already fetched beside this by the "Runs on" card and
  // cached under the same query key as the list page. Unknown reads as runnable: a
  // list still in flight must not tell the user their key is outside the pipeline,
  // and the route refuses a non-runnable key with a 400 in any case.
  const keysQuery = usePromptKeys();
  // The two chatbot keys are `dry_runnable: false` because they are not assistant-pipeline
  // nodes, and that is still true - what changed at AC-807 is that their Test runs a
  // dry-run chatbot TURN instead, which does exercise them. So the box stays enabled for
  // them and `DryRunBox` decides which of the two things to run.
  const dryRunnable =
    CHATBOT_TURN_PROMPT_KEYS.includes(name) ||
    (keysQuery.data?.find((row) => row.name === name)?.dry_runnable ?? true);

  // Which version is loaded as the editor base. Defaults to production.
  const [baseVersion, setBaseVersion] = useState<number | null>(null);
  useEffect(() => {
    if (baseVersion == null && productionVersion != null) setBaseVersion(productionVersion);
  }, [productionVersion, baseVersion]);

  const baseQuery = usePromptVersion(name, baseVersion);
  const [draft, setDraft] = useState<string>('');
  const [commitMessage, setCommitMessage] = useState('');
  const [loadedFor, setLoadedFor] = useState<string>(''); // `${name}:${version}` currently in the buffer

  // Load the fetched base template into the draft buffer once per version load.
  useEffect(() => {
    if (baseQuery.data && loadedFor !== `${name}:${baseQuery.data.version}`) {
      setDraft(baseQuery.data.template);
      setLoadedFor(`${name}:${baseQuery.data.version}`);
    }
  }, [baseQuery.data, name, loadedFor]);

  // Slice E: a key with no saved version has no `baseQuery.data` to seed
  // from at all - the effect above never fires, and the editor opened
  // empty (browser finding on every `ai_extract_*` key the day they were
  // registered). `meta.fallback_text` - the code default - is what seeds it
  // instead, once, the same "one load per key" shape as the effect above.
  useEffect(() => {
    if (meta && meta.versions.length === 0 && loadedFor !== `${name}:fallback`) {
      setDraft(meta.fallback_text ?? '');
      setLoadedFor(`${name}:fallback`);
    }
  }, [meta, name, loadedFor]);

  const dirty = !!baseQuery.data && draft !== baseQuery.data.template;

  // Warn on navigate-away when there are unsaved edits (PLAN §9b Q4).
  useEffect(() => {
    if (!dirty) return;
    const handler = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = '';
    };
    window.addEventListener('beforeunload', handler);
    return () => window.removeEventListener('beforeunload', handler);
  }, [dirty]);

  const declared = useMemo(() => meta?.variables ?? [], [meta]);
  // PLAN-prompt-dynamic-30sep R5a: tokens the backend fills from registry tables. They are
  // chips in the editor, always known to validation and never "missing".
  const registryNames = useMemo(() => meta?.registry_variables ?? [], [meta]);
  const hasRegistry = registryNames.length > 0;
  const registryQuery = useRegistryVariables(name, hasRegistry);
  const registryRows = useMemo(() => registryQuery.data ?? [], [registryQuery.data]);
  const [editorMode, setEditorMode] = useState<'edit' | 'preview'>('edit');
  const chipEditorRef = useRef<PromptChipEditorHandle>(null);
  const validation = useMemo(
    () => validateVars(draft, declared, registryNames),
    [draft, declared, registryNames],
  );
  const hasUnknownTokens = validation.unknown.length > 0;

  const saveMut = useSaveVersion(name);
  const labelMut = useSetLabel(name);

  const [diffOpen, setDiffOpen] = useState(false);
  const [diffAgainst, setDiffAgainst] = useState<number | null>(null);
  const diffAgainstQuery = usePromptVersion(name, diffOpen ? diffAgainst : null);

  const [publishState, setPublishState] = useState<{
    open: boolean;
    version: number | null;
    versionId: string | null;
    label: 'production' | 'staging';
  }>({ open: false, version: null, versionId: null, label: 'production' });

  // Version-switch discard confirm (AlertDialog, never window.confirm - CRUD UX
  // standard / feedback_confirm_before_delete_or_unlink).
  const [pendingSwitch, setPendingSwitch] = useState<number | null>(null);

  const saveInputRef = useRef<HTMLInputElement>(null);

  const switchToVersion = (version: number) => {
    setBaseVersion(version);
    setLoadedFor('');
  };

  const requestSwitch = (version: number) => {
    if (version === baseVersion) return;
    if (dirty) {
      setPendingSwitch(version);
      return;
    }
    switchToVersion(version);
  };

  if (versionsQuery.isLoading) {
    return (
      <Container>
        <SectionSkeleton rows={6} className="py-6" />
      </Container>
    );
  }
  if (versionsQuery.isError || !meta) {
    return (
      <Container>
        <p className="text-sm text-destructive">
          {(versionsQuery.error as Error)?.message || 'Failed to load prompt'}
        </p>
      </Container>
    );
  }

  const doSave = () => {
    if (!commitMessage.trim()) {
      toast.error('Commit message is required');
      saveInputRef.current?.focus();
      return;
    }
    if (hasUnknownTokens) {
      toast.error('Remove unknown template tokens before saving');
      return;
    }
    saveMut.mutate(
      { template: draft, commit_message: commitMessage.trim() },
      {
        onSuccess: (v) => {
          const missing = v.missing_vars ?? validation.missing;
          if (missing.length) {
            toast.warning(`Saved v${v.version}. Note: declared var(s) not used - ${missing.join(', ')}`);
          } else {
            toast.success(`Saved v${v.version}`);
          }
          setCommitMessage('');
          setBaseVersion(v.version);
          setLoadedFor(''); // force reload of the new version into the buffer
        },
        onError: (e: Error & { validation?: SaveVersionError }) => {
          if (e.validation?.unknown_tokens?.length) {
            toast.error(`Unknown token(s): ${e.validation.unknown_tokens.join(', ')}`);
          } else {
            toast.error(e.message);
          }
        },
      },
    );
  };

  const doPublish = () => {
    if (!publishState.versionId) return;
    labelMut.mutate(
      { label: publishState.label, version_id: publishState.versionId },
      {
        onSuccess: () => {
          toast.success(`Moved ${publishState.label} to v${publishState.version}`);
          setPublishState((s) => ({ ...s, open: false }));
        },
        onError: (e: Error) => toast.error(e.message),
      },
    );
  };

  const baseVersionId = baseQuery.data?.id ?? null;

  return (
    <Container className="space-y-4">
      <PageHeader
        title={
          <span className="inline-flex flex-wrap items-center gap-2">
            <span className="font-mono">{name}</span>
            {!meta.active ? (
              <Badge variant="warning" className="text-xs">
                Dormant · activates in {meta.activates_in}
              </Badge>
            ) : null}
          </span>
        }
        // The title carries a badge, so the trail is told the name separately:
        // without it the trail would end on "Prompts" and stop linking there.
        crumbTitle={name}
        actions={
          <Button variant="outline" asChild>
            <Link href="/system-management/ai-assistant/prompts">
              <ArrowLeft className="size-4" /> Back to prompts
            </Link>
          </Button>
        }
      />
      <p className="text-sm text-muted-foreground break-words">{meta.role}</p>

      {!meta.active ? (
        <div className="rounded-md border border-amber-300 bg-amber-50 p-3 text-xs text-amber-800">
          Inactive - this prompt is saved but not used at runtime yet. It activates in milestone{' '}
          {meta.activates_in}. You can draft ahead; dry-run is disabled until it has a call site.
        </div>
      ) : null}

      <div
        className={cn(
          'grid gap-4 lg:grid-cols-[280px_minmax(0,1fr)]',
          hasRegistry && '2xl:grid-cols-[260px_minmax(0,1fr)_300px]',
        )}
      >
        <div className="flex min-w-0 flex-col gap-4">
        <AgentModelCard name={name} canEdit={canEdit} />

        {/* Version history */}
        <Card className="h-fit">
          <CardHeader>
            <CardTitle className="text-base">Version history</CardTitle>
          </CardHeader>
          <CardContent className="space-y-1.5">
            {meta.versions.map((v) => {
              const isBase = v.version === baseVersion;
              return (
                // Not a <button>: it holds real Publish/Stage <button>s (invalid
                // to nest interactive-in-interactive). Clickable div + keyboard.
                <div
                  key={v.id}
                  role="button"
                  tabIndex={0}
                  onClick={() => requestSwitch(v.version)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault();
                      requestSwitch(v.version);
                    }
                  }}
                  className={cn(
                    'w-full cursor-pointer rounded-md border p-2 text-left text-xs transition-colors',
                    isBase ? 'border-primary bg-primary/5' : 'hover:bg-muted/50',
                  )}
                  data-testid={`version-row-${v.version}`}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-medium">v{v.version}</span>
                    <span className="flex gap-1">
                      {v.labels.includes('production') ? (
                        <Badge variant="success" className="text-[10px]">
                          ● production
                        </Badge>
                      ) : null}
                      {v.labels.includes('staging') ? (
                        <Badge variant="warning" className="text-[10px]">
                          staging
                        </Badge>
                      ) : null}
                    </span>
                  </div>
                  <p className="mt-1 truncate text-muted-foreground" title={v.commit_message || ''}>
                    {v.commit_message || <span className="italic">(no message)</span>}
                  </p>
                  <p className="mt-0.5 text-[11px] text-muted-foreground">
                    {v.created_by_name || 'unknown'}
                  </p>
                  {canEdit && meta.active ? (
                    <div className="mt-2 flex gap-1">
                      {!v.labels.includes('production') ? (
                        <button
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation();
                            setPublishState({ open: true, version: v.version, versionId: v.id, label: 'production' });
                          }}
                          className="rounded bg-emerald-600 px-1.5 py-0.5 text-[10px] font-medium text-white hover:bg-emerald-700"
                          data-testid={`publish-production-${v.version}`}
                        >
                          Publish
                        </button>
                      ) : null}
                      {!v.labels.includes('staging') ? (
                        <button
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation();
                            setPublishState({ open: true, version: v.version, versionId: v.id, label: 'staging' });
                          }}
                          className="rounded bg-amber-500 px-1.5 py-0.5 text-[10px] font-medium text-white hover:bg-amber-600"
                          data-testid={`publish-staging-${v.version}`}
                        >
                          Stage
                        </button>
                      ) : null}
                    </div>
                  ) : null}
                </div>
              );
            })}
          </CardContent>
        </Card>
        </div>

        {/* Editor + tools */}
        <div className="min-w-0 space-y-4">
          <Card>
            <CardHeader className="flex flex-row items-center justify-between gap-2">
              <CardTitle className="text-base">
                Editor{' '}
                <span className="text-xs font-normal text-muted-foreground">
                  {/* Slice E: no saved version means `baseVersion` never
                      resolves, so "v{baseVersion}" would print "vnull" - the
                      draft came off `fallback_text` instead, and the label
                      says so. */}
                  (base: {meta?.versions.length === 0 ? 'code default' : `v${baseVersion}`}
                  {dirty ? ' · unsaved changes' : ''})
                </span>
              </CardTitle>
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => {
                  setDiffOpen((o) => !o);
                  if (diffAgainst == null) {
                    const other = meta.versions.find((v) => v.version !== baseVersion);
                    setDiffAgainst(other?.version ?? baseVersion);
                  }
                }}
                data-testid="diff-toggle"
              >
                <GitCompare className="size-4" /> {diffOpen ? 'Hide diff' : 'Diff'}
              </Button>
            </CardHeader>
            <CardContent className="space-y-3">
              {hasRegistry ? (
                <Tabs value={editorMode} onValueChange={(v) => setEditorMode(v as 'edit' | 'preview')}>
                  <TabsList shape="pill" size="sm" aria-label="Editor mode">
                    <TabsTrigger value="edit" data-testid="editor-mode-edit">
                      Edit
                    </TabsTrigger>
                    <TabsTrigger value="preview" data-testid="editor-mode-preview">
                      Preview rendered prompt
                    </TabsTrigger>
                  </TabsList>
                </Tabs>
              ) : null}
              {hasRegistry && editorMode === 'preview' ? (
                <RenderedPreview template={draft} registryNames={registryNames} rows={registryRows} />
              ) : null}
              {hasRegistry ? (
                // Stays mounted (hidden) under Preview, so the owner's last caret survives a
                // look at the preview and the wired panel's Insert still lands there.
                <div hidden={editorMode === 'preview'} data-testid="chip-editor-pane">
                  <PromptChipEditor
                    ref={chipEditorRef}
                    value={draft}
                    onChange={setDraft}
                    variables={registryRows}
                    registryNames={registryNames}
                    disabled={!canEdit}
                  />
                </div>
              ) : (
                <SearchableTextarea
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  className="min-h-[320px] font-mono text-xs"
                  spellCheck={false}
                  disabled={!canEdit}
                  data-testid="prompt-editor"
                />
              )}
              <VarChips validation={validation} declared={declared} />

              {diffOpen ? (
                <div className="space-y-2 rounded-md border p-3">
                  <div className="flex items-center gap-2 text-xs">
                    <span className="text-muted-foreground">Compare current draft against:</span>
                    <div data-testid="diff-base-select">
                      <SearchableSelect
                        value={diffAgainst != null ? String(diffAgainst) : ''}
                        onChange={(v) => setDiffAgainst(Number(v))}
                        options={meta.versions.map((v) => ({
                          value: String(v.version),
                          label: `v${v.version}`,
                        }))}
                        placeholder="Select version"
                        size="sm"
                        triggerClassName="w-28"
                      />
                    </div>
                  </div>
                  {diffAgainstQuery.data ? (
                    <DiffView
                      a={diffAgainstQuery.data.template}
                      b={draft}
                      aLabel={`v${diffAgainstQuery.data.version}`}
                      bLabel={dirty ? 'current draft' : `v${baseVersion}`}
                    />
                  ) : (
                    <SectionSkeleton rows={2} className="text-xs" />
                  )}
                </div>
              ) : null}

              {canEdit ? (
                <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
                  <div className="flex-1 space-y-1">
                    <label className="text-xs text-muted-foreground">Commit message (required)</label>
                    <Input
                      ref={saveInputRef}
                      value={commitMessage}
                      onChange={(e) => setCommitMessage(e.target.value)}
                      placeholder="What changed and why?"
                      data-testid="commit-message"
                    />
                  </div>
                  <Button
                    type="button"
                    onClick={doSave}
                    disabled={saveMut.isPending || hasUnknownTokens || !commitMessage.trim() || !dirty}
                    data-testid="save-version"
                  >
                    {saveMut.isPending ? (
                      <Loader2 className="size-4 animate-spin" />
                    ) : (
                      <>
                        <Save className="size-4" /> Save as new version
                      </>
                    )}
                  </Button>
                </div>
              ) : (
                <p className="text-xs text-muted-foreground">You have read-only access to prompts.</p>
              )}
            </CardContent>
          </Card>

          <DryRunBox
            keyName={name}
            versionId={baseVersionId}
            versionLabel={`v${baseVersion}`}
            disabled={!meta.active || !dryRunnable}
            disabledReason={
              !meta.active
                ? `Dormant key - no runtime call site until ${meta.activates_in}.`
                : !dryRunnable
                  ? 'This key is not part of the assistant pipeline - the dry-run would not exercise it.'
                  : undefined
            }
          />
        </div>

        {hasRegistry ? (
          <div className="min-w-0 lg:col-span-2 2xl:col-span-1">
            <WiredPanel
              variables={registryRows}
              draft={draft}
              isLoading={registryQuery.isLoading}
              canEdit={canEdit}
              onInsert={(varName) => {
                // At the owner's last caret in the editor, never appended at the bottom
                // (owner hand test #1405, item 2).
                setEditorMode('edit');
                chipEditorRef.current?.insertVariable(varName);
              }}
            />
          </div>
        ) : null}
      </div>

      <PublishDialog
        open={publishState.open}
        onOpenChange={(o) => setPublishState((s) => ({ ...s, open: o }))}
        keyName={name}
        version={publishState.version}
        label={publishState.label}
        pending={labelMut.isPending}
        onConfirm={doPublish}
      />

      <AlertDialog open={pendingSwitch != null} onOpenChange={(o) => !o && setPendingSwitch(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Discard unsaved edits?</AlertDialogTitle>
            <AlertDialogDescription>
              You have unsaved changes in the editor. Loading v{pendingSwitch} will discard them. This cannot be
              undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Keep editing</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              onClick={() => {
                if (pendingSwitch != null) switchToVersion(pendingSwitch);
                setPendingSwitch(null);
              }}
            >
              Discard &amp; load v{pendingSwitch}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Container>
  );
}

/** Malaysia time, in the form the chatbot's `{{current_date}}` directive uses ("Wednesday, 30 September 2026"). */
function todayInMalaysia(): string {
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat('en-GB', {
      timeZone: 'Asia/Kuala_Lumpur',
      weekday: 'long',
      day: '2-digit',
      month: 'long',
      year: 'numeric',
    })
      .formatToParts(new Date())
      .map((p) => [p.type, p.value]),
  );
  return `${parts.weekday}, ${parts.day} ${parts.month} ${parts.year}`;
}

/**
 * "Preview rendered prompt" (R5a): the draft with every registry variable replaced by the
 * text the model receives right now, each lightly tinted so its source stays visible.
 * Read-only; `{{current_date}}` shows today.
 */
function RenderedPreview({
  template,
  registryNames,
  rows,
}: {
  template: string;
  registryNames: string[];
  rows: { name: string; label: string; rendered: string }[];
}) {
  const byName = new Map(rows.map((r) => [r.name, r]));
  return (
    <pre
      data-testid="prompt-preview"
      aria-readonly="true"
      className="max-h-[70dvh] min-h-[320px] overflow-auto whitespace-pre-wrap break-words rounded-md border bg-muted/20 p-3 font-mono text-xs leading-relaxed"
    >
      {splitTemplate(template, registryNames).map((seg, i) =>
        seg.kind === 'text' ? (
          <span key={i}>{seg.text.replace(/\{\{\s*current_date\s*\}\}/g, todayInMalaysia())}</span>
        ) : (
          <span key={i} className="rounded-sm bg-primary/10" title={byName.get(seg.name)?.label ?? seg.name}>
            {byName.get(seg.name)?.rendered ?? ''}
          </span>
        ),
      )}
    </pre>
  );
}
