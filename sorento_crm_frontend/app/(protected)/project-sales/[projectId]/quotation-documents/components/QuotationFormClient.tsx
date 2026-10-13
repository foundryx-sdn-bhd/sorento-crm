'use client';

import * as React from 'react';
import { useRouter } from 'next/navigation';
import { FileText, ListOrdered, Mail, Pencil, Plus, ScrollText, Trash2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { PageHeader } from '@/components/common/PageHeader';
import { useHasPermission } from '@/hooks/usePermissions';
import { projectCrumbs } from '../../../_shared/lib/crumbs';
import {
  useProject,
  useQuotations,
  useQuotationVersions,
} from '../../../_shared/hooks/useProjects';
import {
  useQuotationDocument,
  useQuotationDocumentMutations,
  useQuotationLetterTemplates,
} from '../../../_shared/hooks/useQuotationDocuments';
import type {
  QuotationDocument,
  QuotationDocumentBody,
  QuotationFormScopeBody,
  QuotationScope,
} from '../../../_shared/services/quotationDocumentService';
import type {
  Project,
  ProjectQuotation,
  StagedQuotationLine,
} from '../../../_shared/types/project.types';
import { OutcomePill } from '../../../_shared/components/OutcomePill';
import { isDecimalString, sumMoney } from '../../../_shared/lib/money';
import { QuotationDialog } from '../../components/QuotationDialog';
import {
  QuotationVersionEditor,
  stagedLinesToBody,
  stagedScopeTotal,
  unfinishedStagedLines,
  type QuotationScopeEditing,
} from '../../components/QuotationVersionEditor';
import { QuotationNameDialog } from '../[documentId]/components/QuotationNameDialog';
import { QuotationScopeTabs } from '../[documentId]/components/QuotationScopeTabs';
import { useQuotationEditSession } from '../[documentId]/components/useQuotationEditSession';
import {
  QuotationDocumentHeader,
  useQuotationHeaderDetails,
} from '../[documentId]/components/QuotationDocumentHeader';
import {
  QuotationCoverLetterPanel,
  QuotationTermsPanel,
} from '../[documentId]/components/QuotationLetterPanels';

/**
 * One scope as the form holds it until Save: its name, series and notes. Its LINES are not held
 * here. They live in the same staged edit session the quotation page used on origin/main
 * (`useQuotationEditSession`), keyed by `key`, and are edited by the same editor
 * (`QuotationVersionEditor`), so the Lines tab is the quotation page's lines editing moved under
 * the form (#1341, round 3: "we shouldn't revamp the Lines tab, it was good, we should reuse that").
 */
type FormScope = {
  key: string;
  /** The saved scope's id, or null for one added on this form. */
  id: string | null;
  scope_label: string;
  series_id: string;
  /** Undefined until the Edit scope dialog sets it, so an untouched saved note is not sent. */
  notes?: string | null;
};

type FormTab = 'header' | 'lines' | 'cover-letter' | 'terms';

/**
 * The form's tabs, the read page's minus Signatures (#1341, owner: "header stays in header tab").
 * Same order, same labels, same icons, so view and edit read as one layout.
 */
const FORM_TABS: {
  key: FormTab;
  title: string;
  icon: React.ComponentType<{ className?: string }>;
}[] = [
  { key: 'header', title: 'Header', icon: FileText },
  { key: 'lines', title: 'Lines', icon: ListOrdered },
  { key: 'cover-letter', title: 'Cover letter', icon: Mail },
  { key: 'terms', title: 'Terms', icon: ScrollText },
];

const HEADER_FIELDS = [
  'your_ref',
  'doc_date',
  'attn_name',
  'subject_title',
  'recipient_name_snapshot',
  'recipient_address_snapshot',
  'recipient_phone_snapshot',
] as const;

let scopeCounter = 0;
function newScope(): FormScope {
  scopeCounter += 1;
  return {
    key: `new-scope:${scopeCounter}`,
    id: null,
    scope_label: '',
    series_id: '',
  };
}

/**
 * A scope added on this form and never touched: no name, no line. Header-only is a real save
 * (owner on Q3: "yes can, header only is fine"), so this one is left out rather than refused.
 */
function isUntouchedNewScope(scope: FormScope, lines: StagedQuotationLine[]): boolean {
  return !scope.id && !scope.scope_label.trim() && !scope.series_id && lines.length === 0;
}

/** Staged lines whose quantity or unit price is typed but is not a number: the server would 422. */
function badNumberLines(lines: StagedQuotationLine[]): number {
  return lines.filter(
    (line) =>
      !line.removed &&
      [line.draft.quantity, line.draft.unit_price].some(
        (value) => (value ?? '').trim() !== '' && !isDecimalString((value ?? '').trim()),
      ),
  ).length;
}

/** What the strip calls a scope that has no name yet. */
function scopeTitle(scope: FormScope, index: number): string {
  return scope.scope_label.trim() || `Scope ${index + 1}`;
}

/** Today in the browser's own calendar, as the ISO date the API speaks. */
function todayIso(): string {
  const now = new Date();
  const month = String(now.getMonth() + 1).padStart(2, '0');
  const day = String(now.getDate()).padStart(2, '0');
  return `${now.getFullYear()}-${month}-${day}`;
}

/**
 * The quotation form page, create and edit (#1341).
 *
 * The owner: "I should be able to add product straight away and save when I am satisfied, if I
 * want to edit I can click on the gear button to edit". So Add a quotation lands HERE with nothing
 * written, the page holds the letterhead and the scopes with their lines, and Save is ONE request:
 * a POST that creates the quotation, its scopes and every line in one transaction, or, in edit
 * mode, one PATCH carrying the header and every scope. Cancel leaves exactly what was there.
 *
 * Edit is the same page with the same tabs in the same order (view and edit share a layout):
 * Header, Lines, Cover letter, Terms, the read page's own tabs minus Signatures. The owner: "header
 * stays in header tab", and the letter and terms "show them on create as well", prefilled from the
 * company templates. Status rules are the server's: a scope the customer holds shows its lines
 * read-only, and a scope any version of which was sent cannot be removed.
 */
export function QuotationFormClient({
  projectId,
  documentId,
}: {
  projectId: string;
  documentId?: string;
}) {
  const router = useRouter();
  const isEdit = Boolean(documentId);
  const project = useProject(projectId);
  const saved = useQuotationDocument(projectId, documentId);
  const quotations = useQuotations(isEdit ? projectId : undefined);
  const mutations = useQuotationDocumentMutations(projectId, documentId);
  // The company's letter and terms, for the create form's own tabs. Edit reads the saved text.
  const letterTemplates = useQuotationLetterTemplates(projectId, !isEdit);
  // Removing a saved scope is a hard delete: the same grant as the scope DELETE route.
  const canRemoveSaved = useHasPermission('projects.projects.delete');

  const [header, setHeader] = React.useState<QuotationDocumentBody | null>(
    null,
  );
  const [letter, setLetter] = React.useState<QuotationDocumentBody>({});
  /** The create form's letter tabs have taken the templates, once. */
  const [letterSeeded, setLetterSeeded] = React.useState(false);
  const [scopes, setScopes] = React.useState<FormScope[] | null>(null);
  /** The staged lines of every scope opened on this form: the quotation page's own session. */
  const edit = useQuotationEditSession();
  const [activeKey, setActiveKey] = React.useState<string | null>(null);
  const [addingScope, setAddingScope] = React.useState(false);
  const [editingScopeKey, setEditingScopeKey] = React.useState<string | null>(null);
  /** Saved scopes removed on this form, deleted by the one PATCH (#1341, Q2). */
  const [removedIds, setRemovedIds] = React.useState<string[]>([]);
  const [tab, setTab] = React.useState<FormTab>('header');
  const savedScopes = saved.data?.scopes;
  const keptScopes = React.useMemo(
    () => (savedScopes ?? []).filter((scope) => !removedIds.includes(scope.id)),
    [savedScopes, removedIds],
  );
  const details = useQuotationHeaderDetails(isEdit ? projectId : undefined, keptScopes);
  const [error, setError] = React.useState<string | null>(null);
  const [isSaving, setIsSaving] = React.useState(false);
  /** Anything typed since the page opened. Drives the warning on leaving the site. */
  const [dirty, setDirty] = React.useState(false);

  // Everything on this page lives only in the browser until Save, so a refresh or a closed tab
  // would lose it silently. Warn while there is something to lose.
  const isDirty = dirty || edit.changedScopes.length > 0;
  React.useEffect(() => {
    if (!isDirty || isSaving) return;
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = '';
    };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [isDirty, isSaving]);

  // The starting point, taken ONCE: a refetch landing mid-edit must not overwrite what somebody
  // is typing.
  React.useEffect(() => {
    if (header !== null) return;
    if (!isEdit && project.data) {
      // Name, Phone and Attn come from the project's customer master data (owner, 13 Oct);
      // with no customer known yet the letter still goes to the developer.
      setHeader({
        your_ref: '',
        doc_date: todayIso(),
        attn_name: project.data.customer_attn ?? '',
        subject_title: project.data.title ?? '',
        recipient_name_snapshot:
          project.data.customer_name ?? project.data.developer_name ?? '',
        recipient_address_snapshot: '',
        recipient_phone_snapshot: project.data.customer_phone ?? '',
      });
      setScopes([newScope()]);
    }
    if (isEdit && saved.data && quotations.data) {
      const document = saved.data;
      setHeader(
        Object.fromEntries(
          HEADER_FIELDS.map((field) => [
            field,
            (document[field] as string | null) ?? '',
          ]),
        ) as QuotationDocumentBody,
      );
      setScopes(
        document.scopes.map((scope) => ({
          key: scope.id,
          id: scope.id,
          scope_label: scope.scope_label,
          series_id:
            (quotations.data ?? []).find((row) => row.id === scope.id)
              ?.series_id ?? '',
        })),
      );
    }
  }, [header, isEdit, project.data, quotations.data, saved.data]);

  // Create: the letter and terms tabs start from the company templates, taken once so a late
  // refetch cannot overwrite what was typed. No template leaves the tab empty and editable.
  React.useEffect(() => {
    if (isEdit || letterSeeded || !letterTemplates.isFetched) return;
    setLetter({
      cover_letter_html: letterTemplates.data?.cover_letter_html ?? '',
      terms_html: letterTemplates.data?.terms_html ?? '',
    });
    setLetterSeeded(true);
  }, [isEdit, letterSeeded, letterTemplates.isFetched, letterTemplates.data]);

  const updateScope = React.useCallback(
    (key: string, patch: Partial<FormScope>) => {
      setDirty(true);
      setScopes((previous) =>
        (previous ?? []).map((scope) =>
          scope.key === key ? { ...scope, ...patch } : scope,
        ),
      );
    },
    [],
  );

  /** The lines staged for a scope, or none while it has not been opened on the Lines tab. */
  const stagedScopes = edit.scopes;
  const stagedLinesOf = React.useCallback(
    (key: string): StagedQuotationLine[] => stagedScopes[key]?.lines ?? [],
    [stagedScopes],
  );

  /**
   * Each scope's figure: its staged lines once opened, the server's own total until then. The
   * same rule the quotation page's header used for its live total.
   */
  const scopeTotal = React.useCallback(
    (scope: FormScope): string =>
      (stagedScopes[scope.key] ? stagedScopeTotal(stagedScopes[scope.key].lines) : null) ??
      savedScopes?.find((row) => row.id === scope.id)?.scope_total ??
      '0',
    [savedScopes, stagedScopes],
  );

  const liveTotal = React.useMemo(
    () => sumMoney((scopes ?? []).map(scopeTotal)),
    [scopeTotal, scopes],
  );

  const backPath = isEdit
    ? `/project-sales/${projectId}/quotation-documents/${documentId}`
    : `/project-sales/${projectId}?tab=quotations`;
  const title = isEdit
    ? (saved.data?.document_no ?? 'Edit quotation')
    : 'New quotation';
  const letterReady = isEdit || letterSeeded;

  /** The scopes Save sends: an untouched new scope is not one (header-only save). */
  function scopesToSave(): FormScope[] {
    return (scopes ?? []).filter(
      (scope) => !isUntouchedNewScope(scope, stagedLinesOf(scope.key)),
    );
  }

  function validate(): string | null {
    const list = scopesToSave();
    if (list.some((scope) => !scope.scope_label.trim())) {
      return 'Every scope needs a name, e.g. Townhouse or Guard House.';
    }
    const unfinished = list.reduce(
      (total, scope) => total + unfinishedStagedLines(stagedLinesOf(scope.key)),
      0,
    );
    if (unfinished > 0) {
      return unfinished === 1
        ? 'One line still needs a product or a description.'
        : `${unfinished} lines still need a product or a description.`;
    }
    // Caught here rather than as a 422 toast: the field already says "Must be a number".
    const badNumbers = list.reduce(
      (total, scope) => total + badNumberLines(stagedLinesOf(scope.key)),
      0,
    );
    if (badNumbers > 0) {
      return badNumbers === 1
        ? 'One line has a quantity or unit price that is not a number.'
        : `${badNumbers} lines have a quantity or unit price that is not a number.`;
    }
    return null;
  }

  function headerBody(): QuotationDocumentBody {
    const body: QuotationDocumentBody = {};
    HEADER_FIELDS.forEach((field) => {
      const value = ((header ?? {})[field] as string | null | undefined) ?? '';
      (body as Record<string, string | null>)[field] = value.trim()
        ? value
        : null;
    });
    return body;
  }

  function scopesBody(): QuotationFormScopeBody[] {
    const changed = new Set(edit.changedScopes.map((scope) => scope.scopeId));
    return scopesToSave().map((scope) => {
      const item: QuotationFormScopeBody = {
        scope_label: scope.scope_label.trim(),
        series_id: scope.series_id || null,
      };
      if (scope.id) item.id = scope.id;
      if (scope.notes !== undefined) item.notes = scope.notes;
      // A new scope carries whatever was staged under it. A saved one carries its lines only when
      // they moved, the rule the quotation page's Save kept: the write replaces the WHOLE set, and
      // a scope the customer holds is never staged, so it is never sent.
      if (!scope.id) item.lines = stagedLinesToBody(stagedLinesOf(scope.key));
      else if (changed.has(scope.key)) item.lines = stagedLinesToBody(stagedLinesOf(scope.key));
      return item;
    });
  }

  /** Create: the letter tabs' text, when there is any. Empty lets the server render the template. */
  function createLetterBody(): QuotationDocumentBody {
    const body: QuotationDocumentBody = {};
    if ((letter.cover_letter_html ?? '').trim()) body.cover_letter_html = letter.cover_letter_html;
    if ((letter.terms_html ?? '').trim()) body.terms_html = letter.terms_html;
    return body;
  }

  async function save() {
    const problem = validate();
    setError(problem);
    // Every refusal is about a scope or a line, so show the tab it is on.
    if (problem) {
      setTab('lines');
      return;
    }
    setIsSaving(true);
    try {
      if (isEdit && documentId) {
        await mutations.update.mutateAsync({
          id: documentId,
          body: {
            ...headerBody(),
            ...letter,
            scopes: scopesBody(),
            ...(removedIds.length > 0 ? { remove_scope_ids: removedIds } : {}),
          },
        });
        router.push(
          `/project-sales/${projectId}/quotation-documents/${documentId}`,
        );
      } else {
        const created = await mutations.create.mutateAsync({
          ...headerBody(),
          ...createLetterBody(),
          scopes: scopesBody(),
        });
        router.push(
          `/project-sales/${projectId}/quotation-documents/${created.id}`,
        );
      }
    } catch {
      // The mutation toasted the server's reason; the form keeps everything that was typed.
    } finally {
      setIsSaving(false);
    }
  }

  const loading =
    project.isLoading ||
    (isEdit && (saved.isLoading || quotations.isLoading)) ||
    !header;
  const failed = isEdit && (saved.isError || (!saved.isLoading && !saved.data));

  const shownDocument: QuotationDocument = {
    ...(saved.data ?? placeholderDocument(projectId)),
    ...(header ?? {}),
  } as QuotationDocument;

  return (
    <div className="space-y-6">
      <PageHeader
        title={title}
        crumbs={projectCrumbs(projectId, { title })}
        actions={
          <>
            <Button
              type="button"
              variant="outline"
              onClick={() => router.push(backPath)}
            >
              Cancel
            </Button>
            <Button
              type="button"
              disabled={isSaving || loading || failed || !letterReady}
              onClick={() => void save()}
            >
              {isSaving ? 'Saving...' : 'Save quotation'}
            </Button>
          </>
        }
      />

      {error && (
        <p
          role="alert"
          className="rounded-md border border-destructive/40 bg-destructive/5 px-4 py-2 text-sm text-destructive"
        >
          {error}
        </p>
      )}

      {failed ? (
        <div className="rounded-lg border border-destructive/40 bg-destructive/5 px-6 py-10 text-center">
          <h2 className="text-sm font-semibold text-destructive">
            This quotation could not be loaded
          </h2>
        </div>
      ) : loading ? (
        <div className="space-y-4">
          <Skeleton className="h-48 w-full" />
          <Skeleton className="h-64 w-full" />
        </div>
      ) : (
        <Tabs value={tab} onValueChange={(value) => setTab(value as FormTab)} className="min-w-0">
          {/* The strip scrolls in its own gutter so the page never drags sideways at 375px. */}
          <div className="min-w-0 overflow-x-auto">
            <TabsList variant="line" className="mb-4 w-max">
              {FORM_TABS.map(({ key, title: tabTitle, icon: Icon }) => (
                <TabsTrigger key={key} value={key}>
                  <Icon className="size-4" aria-hidden />
                  <span>{tabTitle}</span>
                </TabsTrigger>
              ))}
            </TabsList>
          </div>

          {/* Every panel stays mounted and is only hidden: a saved scope's lines load inside the
              Lines tab, and Save must be able to wait for them without that tab being opened. */}
          <FormPanel open={tab === 'header'} label="Header">
            <QuotationDocumentHeader
              document={shownDocument}
              liveGrandTotal={liveTotal}
              details={details}
              onChange={(patch) => {
                setDirty(true);
                setHeader((previous) => ({ ...(previous ?? {}), ...patch }));
              }}
            />
          </FormPanel>

          <FormPanel open={tab === 'lines'} label="Lines">
            {project.data && (
              <FormLines
                project={project.data}
                scopes={scopes ?? []}
                savedScopes={savedScopes ?? []}
                quotations={quotations.data ?? []}
                activeKey={activeKey}
                onSelect={setActiveKey}
                scopeTotal={scopeTotal}
                edit={edit}
                canRemoveSaved={canRemoveSaved}
                onAddScope={() => setAddingScope(true)}
                onEditScope={setEditingScopeKey}
                onRemove={(scope) => {
                  setDirty(true);
                  if (scope.id) {
                    const savedId = scope.id;
                    setRemovedIds((previous) => [...previous, savedId]);
                  }
                  setScopes((previous) =>
                    (previous ?? []).filter((row) => row.key !== scope.key),
                  );
                  setActiveKey(null);
                }}
              />
            )}

            {/* The quotation page's own Add a scope dialog. It names the scope, which is staged
                and created by Save, not at once. */}
            <QuotationNameDialog
              open={addingScope}
              onOpenChange={setAddingScope}
              initialLabel={null}
              addTitle="Add a scope"
              renameTitle="Rename scope"
              fieldLabel="Scope name"
              placeholder="e.g. Townhouse, Guard house"
              hint="A part of the development priced on its own."
              onSave={(label) => {
                const scope = { ...newScope(), scope_label: label };
                setDirty(true);
                setScopes((previous) => [...(previous ?? []), scope]);
                setActiveKey(scope.key);
                setAddingScope(false);
              }}
            />

            {/* The quotation page's own Edit scope dialog: name, series and notes, staged. */}
            {editingScopeKey && project.data && (() => {
              const scope = (scopes ?? []).find((row) => row.key === editingScopeKey);
              if (!scope) return null;
              const savedRow = (quotations.data ?? []).find((row) => row.id === scope.id);
              return (
                <QuotationDialog
                  project={project.data}
                  quotation={
                    {
                      ...(savedRow ?? {}),
                      id: scope.id ?? '',
                      scope_label: scope.scope_label,
                      series_id: scope.series_id || null,
                      notes: scope.notes !== undefined ? scope.notes : (savedRow?.notes ?? null),
                    } as ProjectQuotation
                  }
                  onSubmit={(body) =>
                    updateScope(scope.key, {
                      scope_label: body.scope_label,
                      series_id: body.series_id ?? '',
                      notes: body.notes,
                    })
                  }
                  onDone={() => setEditingScopeKey(null)}
                />
              );
            })()}
          </FormPanel>

          <FormPanel open={tab === 'cover-letter'} label="Cover letter">
            {letterReady ? (
              <QuotationCoverLetterPanel
                html={letter.cover_letter_html ?? saved.data?.cover_letter_html ?? ''}
                onChange={(html) => {
                  setDirty(true);
                  setLetter((previous) => ({ ...previous, cover_letter_html: html }));
                }}
              />
            ) : (
              <Skeleton className="h-64 w-full" />
            )}
          </FormPanel>

          <FormPanel open={tab === 'terms'} label="Terms">
            {letterReady ? (
              <QuotationTermsPanel
                html={letter.terms_html ?? saved.data?.terms_html ?? ''}
                onChange={(html) => {
                  setDirty(true);
                  setLetter((previous) => ({ ...previous, terms_html: html }));
                }}
              />
            ) : (
              <Skeleton className="h-64 w-full" />
            )}
          </FormPanel>
        </Tabs>
      )}
    </div>
  );
}

/**
 * One tab's body. Hidden rather than unmounted when another tab is open, so typed work, a saved
 * scope's loading lines and the letter editors all survive a tab switch.
 */
function FormPanel({
  open,
  label,
  children,
}: {
  open: boolean;
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div role="tabpanel" aria-label={label} hidden={!open} className="min-w-0">
      {children}
    </div>
  );
}

/** What the letterhead reads before the server has numbered the quotation. */
function placeholderDocument(projectId: string): QuotationDocument {
  return {
    id: '',
    project_id: projectId,
    document_no: 'Numbered on save',
    our_ref: 'Numbered on save',
    your_ref: null,
    doc_date: null,
    recipient_party_id: null,
    recipient_name_snapshot: null,
    recipient_address_snapshot: null,
    recipient_phone_snapshot: null,
    attn_name: null,
    subject_title: null,
    cover_letter_html: null,
    terms_html: null,
    signatory_name: null,
    signatory_phone: null,
    scopes: [],
    grand_total: '0',
    issue_count: 0,
    current_issue_no: null,
    is_issued: false,
    created_at: null,
    updated_at: null,
  };
}

/**
 * The Lines tab of the form: the quotation page's Lines tab from origin/main, moved here (#1341,
 * round 3). The same scope strip (`QuotationScopeTabs`, with Add a scope at its end), the same
 * scope card (name with its Edit scope pen, outcome), and under it the same `QuotationVersionEditor`
 * in its staged edit mode, bound to the same `useQuotationEditSession` handlers the quotation
 * page's Edit used. Only one scope's editor is mounted at a time, exactly as there; the staged
 * lines live in the session, so switching scope or tab loses nothing.
 */
function FormLines({
  project,
  scopes,
  savedScopes,
  quotations,
  activeKey,
  onSelect,
  scopeTotal,
  edit,
  canRemoveSaved,
  onAddScope,
  onEditScope,
  onRemove,
}: {
  project: Project;
  scopes: FormScope[];
  savedScopes: QuotationScope[];
  quotations: ProjectQuotation[];
  activeKey: string | null;
  onSelect: (key: string) => void;
  scopeTotal: (scope: FormScope) => string;
  edit: ReturnType<typeof useQuotationEditSession>;
  canRemoveSaved: boolean;
  onAddScope: () => void;
  onEditScope: (key: string) => void;
  onRemove: (scope: FormScope) => void;
}) {
  const active = scopes.find((scope) => scope.key === activeKey) ?? scopes[0] ?? null;
  const savedRow = active?.id ? (quotations.find((row) => row.id === active.id) ?? null) : null;

  // Whether any version of the open scope was sent: such a scope cannot be removed (#1341, owner
  // on Q2: "yes can", while nothing in it has been issued). The same cached query its editor uses.
  const versions = useQuotationVersions(active?.id ?? undefined);
  const issued = (versions.data ?? []).some((version) => Boolean(version.is_issued));
  const canRemove =
    active !== null &&
    (!active.id || (canRemoveSaved && Boolean(versions.data) && !issued));

  /** The open scope's edit handles, bound to its key, exactly as the quotation page bound them. */
  const { scopes: stagedScopes, seedScope, stageScope, toggleRemoved } = edit;
  const activeKeyForEdit = active?.key ?? null;
  const scopeEditing = React.useMemo<QuotationScopeEditing | null>(() => {
    if (!activeKeyForEdit) return null;
    const key = activeKeyForEdit;
    return {
      staged: stagedScopes[key]?.lines ?? null,
      seed: (versionId, lines) => seedScope(key, versionId, lines),
      stage: (lines) => stageScope(key, lines),
      toggleRemoved: (lineKey) => toggleRemoved(key, lineKey),
    };
  }, [activeKeyForEdit, seedScope, stageScope, stagedScopes, toggleRemoved]);

  if (!active) {
    return (
      <Card>
        <CardContent className="px-6 py-10 text-center">
          <h3 className="text-sm font-semibold">No scopes on this quotation yet</h3>
          <Button type="button" variant="outline" size="sm" className="mt-4" onClick={onAddScope}>
            <Plus className="size-4" aria-hidden />
            Add a scope
          </Button>
        </CardContent>
      </Card>
    );
  }

  const strip: QuotationScope[] = scopes.map((scope, index) => {
    const saved = savedScopes.find((row) => row.id === scope.id);
    return {
      id: scope.key,
      scope_label: scopeTitle(scope, index),
      sort_order: index,
      outcome: saved?.outcome ?? 'open',
      current_version_id: saved?.current_version_id ?? null,
      current_version_no: saved?.current_version_no ?? null,
      line_count: saved?.line_count ?? 0,
      scope_total: scopeTotal(scope),
    };
  });

  return (
    <div className="space-y-5">
      <QuotationScopeTabs
        scopes={strip}
        activeScopeId={active.key}
        onSelect={onSelect}
        canEdit={project.can_edit}
        onAddScope={onAddScope}
      />

      <section aria-label={scopeTitle(active, scopes.indexOf(active))}>
        <Card>
          <CardHeader className="flex flex-col items-start gap-3 border-b border-border sm:flex-row sm:items-center sm:justify-between">
            {/* Owner, 13 Oct: the pen beside the name opens the Edit scope popup (name and
                series); the series itself is not shown out here. */}
            <div className="flex min-w-0 flex-wrap items-center gap-2">
              <CardTitle className="min-w-0 break-words text-sm">
                {scopeTitle(active, scopes.indexOf(active))}
              </CardTitle>
              <Button
                type="button"
                size="sm"
                variant="ghost"
                mode="icon"
                aria-label="Edit scope"
                title="Edit scope"
                onClick={() => onEditScope(active.key)}
              >
                <Pencil className="size-4" aria-hidden />
              </Button>
              {savedRow && <OutcomePill outcome={savedRow.outcome} />}
            </div>
            {canRemove && (
              <Button
                type="button"
                size="sm"
                variant="destructive"
                onClick={() => onRemove(active)}
              >
                <Trash2 className="size-4" aria-hidden />
                Remove scope
              </Button>
            )}
          </CardHeader>
          {/* min-w-0 is load-bearing: CardContent is a flex item, and without it the line table
              stretches the Card and the whole PAGE scrolls sideways at phone width. */}
          <CardContent className="min-w-0 py-5">
            {active.id && !savedRow ? (
              <Skeleton className="h-64 w-full" />
            ) : (
              <QuotationVersionEditor
                key={active.key}
                project={project}
                quotation={savedRow}
                seriesId={active.series_id || null}
                edit={scopeEditing}
              />
            )}
          </CardContent>
        </Card>
      </section>
    </div>
  );
}
