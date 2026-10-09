'use client';

import { useEffect, useMemo, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { ArrowLeft, Pencil } from 'lucide-react';
import { toast } from '@/lib/toast';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Container } from '@/components/common/container';
import { PageHeader } from '@/components/common/PageHeader';
import { SectionSkeleton } from '@/components/common/SectionSkeleton';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import { useDebouncedValue } from '@/hooks/useDebouncedValue';
import {
  useEmailTemplate,
  usePreviewEmailTemplate,
  usePreviewEmailTemplateDraft,
  useTemplateVariableCatalog,
  useUpdateEmailTemplate,
} from '../hooks/useEmailTemplates';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import type { EmailBlock, EmailLayoutWidth, EmailTemplate } from '../types/emailTemplate.types';
import { buildImplicitBlocks, withBlockIds } from '../lib/emailBlocks';
import BlockListEditor from '../components/BlockListEditor';
import BlockListReadOnly from '../components/BlockListReadOnly';
import EmailPreviewFrame from '../components/EmailPreviewFrame';

interface TemplateDraft {
  name: string;
  description: string;
  subject: string;
  preheader: string;
  bodyText: string;
  isActive: boolean;
  blocks: EmailBlock[];
  width: EmailLayoutWidth;
}

// The card widths the shell renders (EMAIL-HANDOVER-QTY, OI-EMAIL-PRINT): the standard
// 600px card, the 900px one for a table email, or the 672px one that prints on one A4
// portrait page, which the order inquiry handover uses.
const WIDTH_OPTIONS: { value: EmailLayoutWidth; label: string }[] = [
  { value: 'standard', label: 'Standard (600px)' },
  { value: 'wide', label: 'Wide (900px)' },
  { value: 'print', label: 'Print, A4 portrait (672px)' },
];
const DESKTOP_PREVIEW_WIDTH: Record<EmailLayoutWidth, number> = {
  standard: 600,
  wide: 900,
  print: 672,
};

function widthLabel(width: EmailLayoutWidth): string {
  return WIDTH_OPTIONS.find((o) => o.value === width)?.label ?? width;
}

function blocksFor(template: EmailTemplate): EmailBlock[] {
  return template.layout_json
    ? withBlockIds(template.layout_json.blocks)
    : buildImplicitBlocks(template);
}

function widthFor(template: EmailTemplate): EmailLayoutWidth {
  return template.layout_json?.width ?? 'standard';
}

function draftFor(template: EmailTemplate): TemplateDraft {
  return {
    name: template.name,
    description: template.description ?? '',
    subject: template.subject,
    preheader: template.preheader ?? '',
    bodyText: template.body_text ?? '',
    isActive: template.is_active,
    blocks: blocksFor(template),
    width: widthFor(template),
  };
}

export default function EmailTemplateDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const id = params?.id ?? null;
  const { data: template, isLoading, refetch } = useEmailTemplate(id);
  const updateMut = useUpdateEmailTemplate(id ?? '');
  const savedPreviewMut = usePreviewEmailTemplate(id);
  const draftPreviewMut = usePreviewEmailTemplateDraft();
  const variables = useTemplateVariableCatalog(template?.code ?? null);

  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<TemplateDraft | null>(null);

  // The saved-record preview (view mode) refetches whenever the record itself changes.
  useEffect(() => {
    if (id && !editing) savedPreviewMut.mutate(undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, template?.updated_at, editing]);

  const debouncedDraft = useDebouncedValue(draft, 400);

  // The unsaved-draft preview (edit mode, AC-EM031), debounced ~400ms.
  useEffect(() => {
    if (!editing || !debouncedDraft || !template) return;
    draftPreviewMut.mutate({
      subject: debouncedDraft.subject,
      preheader: debouncedDraft.preheader || null,
      layout_json: { version: 1, blocks: debouncedDraft.blocks, width: debouncedDraft.width },
      body_text: debouncedDraft.bodyText || null,
      code: template.code,
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editing, debouncedDraft, template?.code]);

  function startEditing() {
    if (!template) return;
    setDraft(draftFor(template));
    setEditing(true);
  }

  function cancelEditing() {
    setEditing(false);
    setDraft(null);
  }

  async function handleSave() {
    if (!draft) return;
    try {
      await updateMut.mutateAsync({
        name: draft.name.trim(),
        description: draft.description.trim() || null,
        subject: draft.subject.trim(),
        preheader: draft.preheader.trim() || null,
        body_text: draft.bodyText.trim() || null,
        is_active: draft.isActive,
        layout_json: { version: 1, blocks: draft.blocks, width: draft.width },
      });
      toast.success('Email template updated');
      setEditing(false);
      setDraft(null);
      void refetch();
    } catch (err) {
      toast.error((err as Error).message || 'Save failed');
    }
  }

  function patchDraft(fields: Partial<TemplateDraft>) {
    setDraft((prev) => (prev ? { ...prev, ...fields } : prev));
  }

  const variableList = useMemo(() => variables.data?.variables ?? [], [variables.data]);
  // Stable ids for the read-only rows: blocksFor mints ids for a template with no
  // layout_json, and minting them on every render would remount every row.
  const viewBlocks = useMemo(() => (template ? blocksFor(template) : []), [template]);

  async function copyVariable(key: string) {
    const token = `{{ ${key} }}`;
    try {
      await navigator.clipboard.writeText(token);
      toast.success(`Copied ${token}`);
    } catch {
      toast.error('Could not copy - your browser blocked clipboard access');
    }
  }

  const previewSubject = editing ? draftPreviewMut.data?.subject : savedPreviewMut.data?.subject;
  const previewHtml = editing ? draftPreviewMut.data?.body_html : savedPreviewMut.data?.body_html;
  const previewLoading = editing ? draftPreviewMut.isPending : savedPreviewMut.isPending;

  const loading = isLoading || !template;

  return (
    <>
      <Container>
        <PageHeader
          title={template?.name ?? 'Email Template'}
          actions={
            editing ? (
              <>
                <Button variant="outline" size="sm" onClick={cancelEditing} disabled={updateMut.isPending}>
                  Cancel
                </Button>
                <Button size="sm" onClick={handleSave} disabled={updateMut.isPending}>
                  {updateMut.isPending ? 'Saving…' : 'Save'}
                </Button>
              </>
            ) : (
              <>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => router.push('/system-management/email-templates')}
                >
                  <ArrowLeft className="mr-1 size-4" /> Back
                </Button>
                <Button size="sm" onClick={startEditing} disabled={!template}>
                  <Pencil className="mr-1 size-4" /> Edit
                </Button>
              </>
            )
          }
        />
      </Container>

      <Container>
        {loading ? (
          <SectionSkeleton rows={6} />
        ) : (
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <div className="space-y-4">
              <Card>
                <CardHeader>
                  <CardTitle>Template</CardTitle>
                </CardHeader>
                <CardContent className="space-y-3">
                  <div className="flex justify-between gap-2 text-sm">
                    <span className="text-muted-foreground">Code</span>
                    <span className="font-mono">{template.code}</span>
                  </div>
                  <div className="space-y-1">
                    <Label htmlFor="et-name">Name</Label>
                    {editing && draft ? (
                      <Input id="et-name" value={draft.name} onChange={(e) => patchDraft({ name: e.target.value })} />
                    ) : (
                      <p className="text-sm">{template.name}</p>
                    )}
                  </div>
                  <div className="space-y-1">
                    <Label htmlFor="et-description">Description</Label>
                    {editing && draft ? (
                      <Input
                        id="et-description"
                        value={draft.description}
                        onChange={(e) => patchDraft({ description: e.target.value })}
                      />
                    ) : (
                      <p className="text-sm text-muted-foreground">
                        {template.description || 'No description yet.'}
                      </p>
                    )}
                  </div>
                  <div className="space-y-1">
                    <Label htmlFor="et-subject">Subject (Jinja2)</Label>
                    {editing && draft ? (
                      <Input
                        id="et-subject"
                        value={draft.subject}
                        onChange={(e) => patchDraft({ subject: e.target.value })}
                      />
                    ) : (
                      <p className="font-mono text-sm">{template.subject}</p>
                    )}
                  </div>
                  <div className="space-y-1">
                    <Label htmlFor="et-preheader">Preheader</Label>
                    {editing && draft ? (
                      <Input
                        id="et-preheader"
                        value={draft.preheader}
                        onChange={(e) => patchDraft({ preheader: e.target.value })}
                      />
                    ) : (
                      <p className="text-sm text-muted-foreground">
                        {template.preheader || '-'}
                      </p>
                    )}
                  </div>
                  <div className="space-y-1">
                    <Label htmlFor="et-width">Width</Label>
                    {editing && draft ? (
                      <SearchableSelect
                        id="et-width"
                        aria-label="Width"
                        value={draft.width}
                        onChange={(v) => patchDraft({ width: (v as EmailLayoutWidth) || 'standard' })}
                        options={WIDTH_OPTIONS}
                      />
                    ) : (
                      <p className="text-sm text-muted-foreground">{widthLabel(widthFor(template))}</p>
                    )}
                  </div>
                  <div className="space-y-1">
                    <Label htmlFor="et-body-text">Plain text (optional)</Label>
                    {editing && draft ? (
                      <Textarea
                        id="et-body-text"
                        value={draft.bodyText}
                        onChange={(e) => patchDraft({ bodyText: e.target.value })}
                        rows={3}
                        className="font-mono text-sm"
                      />
                    ) : (
                      <p className="text-sm text-muted-foreground">
                        {template.body_text || '-'}
                      </p>
                    )}
                  </div>
                  <div className="flex items-center gap-2">
                    {editing && draft ? (
                      <>
                        <Switch
                          id="et-active"
                          checked={draft.isActive}
                          onCheckedChange={(checked) => patchDraft({ isActive: checked })}
                        />
                        <Label htmlFor="et-active">Active</Label>
                      </>
                    ) : (
                      <>
                        <span className="text-sm text-muted-foreground">Active</span>
                        <Badge variant={template.is_active ? 'success' : 'secondary'}>
                          {template.is_active ? 'Yes' : 'No'}
                        </Badge>
                        {template.is_system && <Badge variant="secondary">System</Badge>}
                      </>
                    )}
                  </div>
                </CardContent>
              </Card>

              <Card>
                <CardHeader>
                  <CardTitle>Blocks</CardTitle>
                </CardHeader>
                <CardContent>
                  {editing && draft ? (
                    <BlockListEditor
                      blocks={draft.blocks}
                      onChange={(blocks) => patchDraft({ blocks })}
                    />
                  ) : (
                    <BlockListReadOnly blocks={viewBlocks} />
                  )}
                </CardContent>
              </Card>

              <Card>
                <CardHeader>
                  <CardTitle>Variables</CardTitle>
                </CardHeader>
                <CardContent>
                  {variableList.length === 0 ? (
                    <p className="text-sm text-muted-foreground">No variables for this template.</p>
                  ) : (
                    <div className="flex max-h-[300px] flex-col gap-1 overflow-y-auto">
                      {variableList.map((v) => (
                        <button
                          key={v.key}
                          type="button"
                          className="rounded px-2 py-1 text-left font-mono text-xs hover:bg-muted"
                          onClick={() => void copyVariable(v.key)}
                          title={v.label}
                        >
                          {`{{ ${v.key} }}`}
                        </button>
                      ))}
                    </div>
                  )}
                </CardContent>
              </Card>
            </div>

            <div className="lg:sticky lg:top-6 lg:h-fit">
              <Card>
                <CardHeader>
                  <CardTitle>Preview (sample data)</CardTitle>
                </CardHeader>
                <CardContent>
                  <EmailPreviewFrame
                    subject={previewSubject}
                    html={previewHtml}
                    isLoading={previewLoading}
                    desktopWidth={
                      DESKTOP_PREVIEW_WIDTH[editing && draft ? draft.width : widthFor(template)]
                    }
                  />
                </CardContent>
              </Card>
            </div>
          </div>
        )}
      </Container>
    </>
  );
}
