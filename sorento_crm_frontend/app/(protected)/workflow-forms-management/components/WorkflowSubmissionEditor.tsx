'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { ArrowLeft, Check, ExternalLink, Link2, Save } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { SectionSkeleton } from '@/components/common/SectionSkeleton';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { useHasPermission } from '@/hooks/usePermissions';
import {
  useAllowedTransitionsQuery,
  useApplyWorkflowTransition,
  useCreateWorkflowSubmission,
  useUpdateWorkflowSubmission,
  useWorkflowSubmissionQuery,
  wfKeys,
} from '../hooks/useWorkflowForms';
import { useDeferredAction } from '@/hooks/useDeferredAction';
import { toast } from '@/lib/toast';
import { DetailActionsMenu } from '@/components/common/DetailActionsMenu';
import {
  DropdownMenuItem,
  DropdownMenuSeparator,
} from '@/components/ui/dropdown-menu';
import {
  fetchPublishedWorkflowDefinitionsForSubmission,
  fetchPublishedWorkflowSchema,
  fetchWorkflowSubmission,
} from '../services/workflowFormsService';
import type { WorkflowFormSchema } from '../types/workflowForms.types';
import { normalizeWorkflowFormSchema } from '../utils/workflowFormSchema';
import { WorkflowHeaderFieldsForm, WorkflowLineGroupsEditor } from './WorkflowDynamicFields';
import {
  getOrigin,
  workflowNewPrefilledUrl,
  workflowSubmissionUrl,
} from '../utils/workflowShareLinks';

type LineRow = { line_group_id: string; sort_order: number; row_data: Record<string, unknown> };

export function NewWorkflowSubmission({
  defaultDefinitionId,
  lockDefinition = false,
}: {
  defaultDefinitionId?: string;
  /** When true, hide the form picker (use with /forms/[id]/new). */
  lockDefinition?: boolean;
}) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const duplicateFrom =
    lockDefinition ? searchParams.get('duplicateFrom')?.trim() || null : null;
  const createMut = useCreateWorkflowSubmission();
  const canAdd = useHasPermission('workflow_forms.submissions.add');
  const [defs, setDefs] = useState<{ id: string; name: string }[]>([]);
  const [defsError, setDefsError] = useState<unknown>(null);
  const [definitionId, setDefinitionId] = useState(defaultDefinitionId ?? '');
  const [schema, setSchema] = useState<WorkflowFormSchema | null>(null);
  const [schemaError, setSchemaError] = useState<string | null>(null);
  const [header, setHeader] = useState<Record<string, unknown>>({});
  const [lines, setLines] = useState<LineRow[]>([]);
  const [duplicateLoading, setDuplicateLoading] = useState(false);
  const duplicateAppliedRef = useRef<string | null>(null);

  const dupKey =
    duplicateFrom && definitionId && lockDefinition ? `${duplicateFrom}:${definitionId}` : null;

  useEffect(() => {
    if (defaultDefinitionId) setDefinitionId(defaultDefinitionId);
  }, [defaultDefinitionId]);

  const loadDefs = useCallback(async () => {
    try {
      const res = await fetchPublishedWorkflowDefinitionsForSubmission();
      setDefs(res.data.map((d) => ({ id: d.id, name: d.name })));
      setDefsError(null);
    } catch (e) {
      setDefs([]);
      setDefsError(e);
    }
  }, []);

  useEffect(() => {
    void loadDefs();
  }, [loadDefs]);

  useEffect(() => {
    if (!definitionId) {
      setSchema(null);
      setSchemaError(null);
      return;
    }
    (async () => {
      try {
        setSchemaError(null);
        const { schema: s } = await fetchPublishedWorkflowSchema(definitionId);
        setSchema(s as WorkflowFormSchema);
        setHeader({});
        setLines([]);
      } catch (e) {
        setSchema(null);
        setSchemaError(e instanceof Error ? e.message : 'Could not load published schema.');
      }
    })();
  }, [definitionId]);

  useEffect(() => {
    duplicateAppliedRef.current = null;
  }, [duplicateFrom, definitionId]);

  useEffect(() => {
    if (!lockDefinition || !dupKey || !duplicateFrom || !definitionId || !schema) return;
    if (duplicateAppliedRef.current === dupKey) return;

    let cancelled = false;
    (async () => {
      setDuplicateLoading(true);
      try {
        const source = await fetchWorkflowSubmission(duplicateFrom);
        if (cancelled) return;
        if (source.definition_id !== definitionId) {
          toast.error('That template submission belongs to a different workflow form.');
          duplicateAppliedRef.current = dupKey;
          return;
        }
        duplicateAppliedRef.current = dupKey;
        setHeader({ ...source.header_data });
        setLines(
          source.lines.map((l, i) => ({
            line_group_id: l.line_group_id,
            sort_order: l.sort_order ?? i,
            row_data: { ...l.row_data },
          })),
        );
        toast.success('Prefilled from existing submission.');
      } catch (e) {
        duplicateAppliedRef.current = dupKey;
        toast.error(e instanceof Error ? e.message : 'Could not load template submission.');
      } finally {
        if (!cancelled) setDuplicateLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [lockDefinition, dupKey, duplicateFrom, definitionId, schema]);

  if (!canAdd) {
    return <p className="text-destructive">You do not have permission to create submissions.</p>;
  }

  const submit = () => {
    if (!definitionId) return;
    createMut.mutate(
      {
        definition_id: definitionId,
        header_data: header,
        lines: lines.map((l, i) => ({ ...l, sort_order: l.sort_order ?? i })),
      },
      {
        onSuccess: (sub) => router.push(`/workflow-forms-management/submissions/${sub.id}`),
      },
    );
  };

  const backHref =
    lockDefinition && definitionId
      ? `/workflow-forms-management/forms/${definitionId}/submissions`
      : '/workflow-forms-management/submissions';

  const formLabel =
    lockDefinition && definitionId
      ? defs.find((d) => d.id === definitionId)?.name ?? 'Workflow form'
      : null;

  return (
    <div className="space-y-4">
      <Button variant="ghost" size="sm" asChild>
        <Link href={backHref}>
          <ArrowLeft className="size-4 mr-1" />
          Back
        </Link>
      </Button>
      <Card>
        <CardHeader>
          <CardTitle>New submission</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          {lockDefinition && definitionId ? (
            <div className="space-y-1 max-w-md">
              <Label>Workflow form</Label>
              <p className="text-sm font-medium">{formLabel}</p>
              <p className="text-xs text-muted-foreground">Only the published version is used for new submissions.</p>
            </div>
          ) : (
            <div className="space-y-1 max-w-md">
              <Label>Workflow form</Label>
              <SearchableSelect
                value={definitionId}
                onChange={setDefinitionId}
                options={defs.map((d) => ({ value: d.id, label: d.name }))}
                loadError={defsError}
                onRetry={() => void loadDefs()}
                placeholder="Select published form…"
              />
              <p className="text-xs text-muted-foreground">Only forms with a published version can accept submissions.</p>
            </div>
          )}
          {duplicateLoading ? (
            <p className="text-sm text-muted-foreground">Loading template from existing submission…</p>
          ) : null}
          {schema ? (
            <>
              <WorkflowHeaderFieldsForm schema={schema} data={header} onChange={setHeader} />
              <WorkflowLineGroupsEditor schema={schema} lines={lines} onChange={setLines} />
              <Button onClick={submit} disabled={createMut.isPending || duplicateLoading}>
                Create
              </Button>
            </>
          ) : definitionId ? (
            <p className="text-amber-600 text-sm">
              {schemaError ?? 'Could not load published schema. Publish the form first.'}
            </p>
          ) : null}
        </CardContent>
      </Card>
    </div>
  );
}

export function WorkflowSubmissionDetail({ submissionId }: { submissionId: string }) {
  const router = useRouter();
  const { data: sub, isLoading, refetch } = useWorkflowSubmissionQuery(submissionId);
  const { data: allowed } = useAllowedTransitionsQuery(submissionId);
  const updateMut = useUpdateWorkflowSubmission(submissionId);
  const applyMut = useApplyWorkflowTransition(submissionId);
  const canEdit = useHasPermission('workflow_forms.submissions.edit');
  const canTransition = useHasPermission('workflow_forms.submissions.transition');
  const canDelete = useHasPermission('workflow_forms.submissions.delete');
  // Delete asks nothing (D7): the countdown replaces the Delete button and
  // Cancel is the way back.
  const deletion = useDeferredAction({
    actionKey: 'workflow_submission.delete',
    entityType: 'workflow_submission',
    entityId: submissionId,
    verb: 'Deleting',
    subject: sub?.definition_name ?? 'this submission',
    surface: 'inline',
    watchFromMount: true,
    successMessage: 'Submission deleted',
    invalidateKeys: [wfKeys.submissions],
    onCommitted: () => router.push(submissionsListHref),
  });

  const [header, setHeader] = useState<Record<string, unknown>>({});
  const [lines, setLines] = useState<LineRow[]>([]);
  const [transitionOpen, setTransitionOpen] = useState(false);
  const [pickedTransition, setPickedTransition] = useState<{ id: string; label: string } | null>(null);
  const [remark, setRemark] = useState('');
  // Which link item is showing its tick. The menu stays open on select so the
  // confirmation can be read where it was asked for (S7-05).
  const [copiedLink, setCopiedLink] = useState<string | null>(null);

  const schema = useMemo((): WorkflowFormSchema | null => {
    const fs = sub?.form_schema;
    if (!fs || typeof fs !== 'object') return null;
    return normalizeWorkflowFormSchema({
      header_fields: fs.header_fields ?? [],
      header_sections: fs.header_sections,
      line_groups: fs.line_groups ?? [],
      states: fs.states ?? [],
      transitions: fs.transitions ?? [],
      notification_rules: fs.notification_rules ?? [],
    });
  }, [sub?.form_schema]);

  useEffect(() => {
    if (!sub) return;
    setHeader({ ...sub.header_data });
    setLines(
      sub.lines.map((l) => ({
        line_group_id: l.line_group_id,
        sort_order: l.sort_order,
        row_data: { ...l.row_data },
      })),
    );
  }, [sub]);

  const terminal = useMemo(() => {
    if (!schema) return false;
    const st = schema.states.find((x) => x.code === sub?.current_state_code);
    return !!st?.is_terminal;
  }, [schema, sub?.current_state_code]);

  const save = () => {
    updateMut.mutate(
      { header_data: header, lines },
      { onSuccess: () => refetch() },
    );
  };

  const openTransition = (t: { id: string; label: string }) => {
    setPickedTransition(t);
    setRemark('');
    setTransitionOpen(true);
  };

  const confirmTransition = () => {
    if (!pickedTransition) return;
    applyMut.mutate(
      { transition_id: pickedTransition.id, remark: remark || undefined },
      {
        onSuccess: () => {
          setTransitionOpen(false);
          refetch();
        },
      },
    );
  };

  if (isLoading || !sub) {
    return <SectionSkeleton rows={6} />;
  }

  const submissionsListHref = `/workflow-forms-management/forms/${sub.definition_id}/submissions`;

  const copyLink = async (url: string, label: string) => {
    try {
      await navigator.clipboard.writeText(url);
      setCopiedLink(label);
      window.setTimeout(() => setCopiedLink(null), 2000);
    } catch {
      toast.error('Could not copy link');
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-2 items-center justify-between">
        <Button variant="ghost" size="sm" asChild>
          <Link href={submissionsListHref}>
            <ArrowLeft className="size-4 mr-1" />
            Back
          </Link>
        </Button>
        <div className="flex flex-wrap gap-2 items-center">
          {canDelete
            ? deletion.countdown ?? (
                <Button
                  size="sm"
                  variant="destructive"
                  disabled={deletion.isPending}
                  onClick={() => deletion.start()}
                >
                  Delete
                </Button>
              )
            : null}
          {canEdit && !terminal && schema ? (
            <Button size="sm" onClick={save} disabled={updateMut.isPending}>
              <Save className="size-4 mr-1" />
              Save submission
            </Button>
          ) : null}
          {canTransition && (allowed?.transitions?.length ?? 0) > 0 ? (
            <>
              {allowed!.transitions.map((t) => (
                <Button key={t.id} size="sm" variant="secondary" onClick={() => openTransition(t)}>
                  {t.label}
                </Button>
              ))}
            </>
          ) : null}
          <DetailActionsMenu ariaLabel="Work links (sign-in required)">
            <DropdownMenuItem
              onSelect={(event) => {
                event.preventDefault();
                const o = getOrigin();
                if (!o) return;
                void copyLink(workflowSubmissionUrl(o, submissionId), 'Work link');
              }}
            >
              {copiedLink === 'Work link' ? (
                <Check className="size-4 mr-2" />
              ) : (
                <Link2 className="size-4 mr-2" />
              )}
              {copiedLink === 'Work link' ? 'Copied' : 'Copy work link'}
            </DropdownMenuItem>
            <DropdownMenuItem
              onSelect={(event) => {
                event.preventDefault();
                const o = getOrigin();
                if (!o) return;
                void copyLink(
                  workflowNewPrefilledUrl(o, sub.definition_id, submissionId),
                  'Prefilled new submission link',
                );
              }}
            >
              {copiedLink === 'Prefilled new submission link' ? (
                <Check className="size-4 mr-2" />
              ) : (
                <Link2 className="size-4 mr-2" />
              )}
              {copiedLink === 'Prefilled new submission link'
                ? 'Copied'
                : 'Copy link: new from this'}
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem
              onClick={() => {
                const o = getOrigin();
                if (!o) return;
                window.open(workflowSubmissionUrl(o, submissionId), '_blank', 'noopener,noreferrer');
              }}
            >
              <ExternalLink className="size-4 mr-2" />
              Open this submission
            </DropdownMenuItem>
            <DropdownMenuItem
              onClick={() => {
                const o = getOrigin();
                if (!o) return;
                window.open(
                  workflowNewPrefilledUrl(o, sub.definition_id, submissionId),
                  '_blank',
                  'noopener,noreferrer',
                );
              }}
            >
              <ExternalLink className="size-4 mr-2" />
              Open new submission from this
            </DropdownMenuItem>
          </DetailActionsMenu>
        </div>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-lg">
            {sub.definition_name || sub.definition_code || 'Submission'}
          </CardTitle>
          <p className="text-sm text-muted-foreground">
            State: <span className="font-medium text-foreground">{sub.current_state_code}</span>
            {sub.form_version_number != null ? ` · Published form version ${sub.form_version_number}` : null}
          </p>
        </CardHeader>
        <CardContent className="space-y-6">
          {!schema ? (
            <p className="text-amber-600 text-sm">
              Form layout could not be loaded. If this persists, ask an administrator to republish the workflow
              form or restore your access.
            </p>
          ) : (
            <>
              <WorkflowHeaderFieldsForm
                schema={schema}
                data={header}
                onChange={setHeader}
                disabled={terminal || !canEdit}
              />
              <WorkflowLineGroupsEditor
                schema={schema}
                lines={lines}
                onChange={setLines}
                disabled={terminal || !canEdit}
              />
            </>
          )}
        </CardContent>
      </Card>

      {sub.transition_logs?.length ? (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">History</CardTitle>
          </CardHeader>
          <CardContent>
            <ul className="text-sm space-y-2">
              {[...sub.transition_logs].reverse().map((log) => (
                <li key={log.id} className="border-b pb-2">
                  <span className="text-muted-foreground">
                    {log.created_at ? new Date(log.created_at).toLocaleString() : ''}
                  </span>{' '}
                  {log.from_state_code ?? '?'} → {log.to_state_code}
                  {log.remark ? ` - ${log.remark}` : ''}
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      ) : null}

      <Dialog open={transitionOpen} onOpenChange={setTransitionOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{pickedTransition?.label}</DialogTitle>
          </DialogHeader>
          <div className="space-y-2">
            <Label>Remark (optional)</Label>
            <Input value={remark} onChange={(e) => setRemark(e.target.value)} />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setTransitionOpen(false)}>
              Cancel
            </Button>
            <Button onClick={confirmTransition} disabled={applyMut.isPending}>
              Confirm transition
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
