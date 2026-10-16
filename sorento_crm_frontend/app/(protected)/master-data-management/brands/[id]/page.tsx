'use client';

import { use, useState } from 'react';
import { useRouter } from 'next/navigation';
import { Check, Loader2, Pencil, Trash2, X } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { NumberInput } from '@/components/ui/number-input';
import { numberFieldError, parseNumberDraft } from '@/lib/number-field';
import { Label } from '@/components/ui/label';
import { Checkbox } from '@/components/ui/checkbox';
import { Switch } from '@/components/ui/switch';
import { Skeleton } from '@/components/ui/skeleton';
import { Textarea } from '@/components/ui/textarea';
import { Container } from '@/components/common/container';
import { PageHeader } from '@/components/common/PageHeader';
import BackToList, { useBackToListHref } from '@/components/common/BackToList';
import DetailActions from '@/components/common/DetailActions';
import type { RecordAction } from '@/components/common/recordActions';
import { useContactAccessTypes } from '@/app/(protected)/user-management/contact-access-types/hooks/useContactAccessTypes';
import { useBrand, useUpdateBrand } from '../hooks/useBrands';
import { useDeferredAction } from '@/hooks/useDeferredAction';
import { formatDate } from '@/lib/helpers';

const LIST_PATH = '/master-data-management/brands';

/**
 * The brand record: one page that reads and edits, in the same layout.
 *
 * A brand used to be edited in a lightbox opened from the row, which meant the
 * record page could only ever be reached by typing a URL - and once there, Edit
 * left for a separate `/edit` screen with a different field order. View and edit
 * are the same layout now (ADR product standards): Edit swaps each value for its
 * input where it stands, Save and Cancel take the primary slot, and the fields
 * the API does not accept stay read-only in both modes rather than pretending.
 */
interface Draft {
  brand_code: string;
  brand_name: string;
  description: string;
  is_active: boolean;
  access_levels: string[];
  flows_to_purchasing: boolean;
  chatbot_weight: string;
  is_searchable: boolean;
}

const Empty = ({ children = 'Not set' }: { children?: string }) => (
  <span className="text-muted-foreground">{children}</span>
);

/** A label above its value, or above its input while editing. */
function Field({
  label,
  htmlFor,
  children,
}: {
  label: string;
  htmlFor?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="grid gap-1.5">
      <Label htmlFor={htmlFor} className="text-sm text-muted-foreground font-normal">
        {label}
      </Label>
      <div className="text-sm font-medium">{children}</div>
    </div>
  );
}

export default function BrandDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const router = useRouter();
  const backHref = useBackToListHref(LIST_PATH);
  const { data: brand, isLoading } = useBrand(id);
  const { data: accessTypeOptions = [] } = useContactAccessTypes();
  const update = useUpdateBrand();

  // Delete asks nothing (D7). The countdown takes the primary button's place and
  // Cancel is the way back; the server applies it when the window lapses, even if
  // this tab is closed first.
  const deletion = useDeferredAction({
    actionKey: 'brand.delete',
    entityType: 'brand',
    entityId: id,
    verb: 'Deleting',
    subject: brand ? `${brand.brand_name} (${brand.brand_code})` : '',
    surface: 'inline',
    watchFromMount: true,
    successMessage: 'Brand deleted',
    invalidateKeys: [['brands'], ['brand-select']],
    onCommitted: () => router.push(backHref),
  });

  const [editing, setEditing] = useState(false);
  const [checked, setChecked] = useState(false);
  const [draft, setDraft] = useState<Draft | null>(null);

  const header = (
    <PageHeader
      title="Brand"
      actions={<BackToList listPath={LIST_PATH} label="Back to brands" />}
    />
  );

  if (isLoading) {
    return (
      <>
        <Container>{header}</Container>
        <Container>
          <div className="space-y-6">
            <Skeleton className="h-10 w-64" />
            <Skeleton className="h-96 w-full" />
          </div>
        </Container>
      </>
    );
  }

  if (!brand) {
    return (
      <>
        <Container>{header}</Container>
        <Container>
          <div className="text-center py-12">
            <p className="text-muted-foreground">Brand not found</p>
            <div className="mt-4 flex justify-center">
              <BackToList listPath={LIST_PATH} label="Back to brands" />
            </div>
          </div>
        </Container>
      </>
    );
  }

  const startEdit = () => {
    setDraft({
      brand_code: brand.brand_code,
      brand_name: brand.brand_name,
      description: brand.description ?? '',
      is_active: brand.is_active,
      access_levels: brand.access_levels ?? [],
      flows_to_purchasing: brand.flows_to_purchasing,
      chatbot_weight: String(brand.chatbot_weight ?? 0),
      is_searchable: brand.is_searchable ?? true,
    });
    setEditing(true);
  };

  const cancelEdit = () => {
    setEditing(false);
    setDraft(null);
    setChecked(false);
  };

  const handleSave = async () => {
    if (!draft) return;
    setChecked(true);
    if (weightProblem(draft.chatbot_weight)) return;
    await update.mutateAsync({
      id,
      data: {
        brand_code: draft.brand_code.trim(),
        brand_name: draft.brand_name.trim(),
        description: draft.description.trim() || undefined,
        is_active: draft.is_active,
        access_levels: draft.access_levels,
        flows_to_purchasing: draft.flows_to_purchasing,
        chatbot_weight: parseNumberDraft(draft.chatbot_weight) as number,
        is_searchable: draft.is_searchable,
      },
    });
    cancelEdit();
  };

  // The dialog's own words (zod in BrandFormDialog), under the input once Save has been pressed.
  const weightProblem = (raw: string): string | undefined => {
    const n = parseNumberDraft(raw);
    if (n === null) return numberFieldError(raw, { min: 0, max: 9999 });
    if (n < 0) return 'Enter 0 or more';
    if (n > 9999) return 'Enter 9999 or less';
    return undefined;
  };
  const weightError = checked && draft ? weightProblem(draft.chatbot_weight) : undefined;

  const canSave =
    !!draft &&
    draft.brand_code.trim().length > 0 &&
    draft.brand_name.trim().length > 0 &&
    !update.isPending;

  const actions: RecordAction[] = [
    {
      key: 'brand.delete',
      label: 'Delete brand',
      icon: Trash2,
      kind: 'destructive',
      disabled: deletion.isPending,
      run: deletion.start,
    },
  ];

  const accessLevelNames = (brand.access_levels ?? []).map(
    (code) => accessTypeOptions.find((opt) => opt.code === code)?.name || code,
  );

  return (
    <>
      <Container>{header}</Container>

      <Container>
        <div className="space-y-6">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
            <div className="space-y-1 min-w-0">
              <div className="flex flex-wrap items-center gap-3">
                <h2 className="text-2xl font-bold break-words min-w-0">{brand.brand_name}</h2>
                <Badge variant={brand.is_active ? 'success' : 'secondary'}>
                  {brand.is_active ? 'Active' : 'Inactive'}
                </Badge>
              </div>
              <p className="text-sm text-muted-foreground">Brand code: {brand.brand_code}</p>
            </div>

            {editing ? (
              <div className="flex flex-wrap items-center justify-end gap-2">
                <Button variant="outline" onClick={cancelEdit} disabled={update.isPending}>
                  <X className="size-4" /> Cancel
                </Button>
                <Button onClick={() => void handleSave()} disabled={!canSave}>
                  {update.isPending ? (
                    <Loader2 className="size-4 animate-spin" />
                  ) : (
                    <Check className="size-4" />
                  )}
                  Save brand
                </Button>
              </div>
            ) : (
              <DetailActions
                actions={actions}
                gearLabel="Brand options"
                pendingAction={deletion.countdown}
                primary={
                  <Button onClick={startEdit}>
                    <Pencil className="size-4" /> Edit
                  </Button>
                }
              />
            )}
          </div>

          <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle>Basic information</CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                <Field label="Brand code" htmlFor="brand-code">
                  {editing && draft ? (
                    <Input
                      id="brand-code"
                      value={draft.brand_code}
                      onChange={(e) =>
                        setDraft({ ...draft, brand_code: e.target.value })
                      }
                    />
                  ) : (
                    brand.brand_code
                  )}
                </Field>

                <Field label="Brand name" htmlFor="brand-name">
                  {editing && draft ? (
                    <Input
                      id="brand-name"
                      value={draft.brand_name}
                      onChange={(e) =>
                        setDraft({ ...draft, brand_name: e.target.value })
                      }
                    />
                  ) : (
                    brand.brand_name
                  )}
                </Field>

                <Field label="Description" htmlFor="brand-description">
                  {editing && draft ? (
                    <Textarea
                      id="brand-description"
                      rows={3}
                      value={draft.description}
                      onChange={(e) =>
                        setDraft({ ...draft, description: e.target.value })
                      }
                    />
                  ) : brand.description ? (
                    <span className="whitespace-pre-wrap font-normal">{brand.description}</span>
                  ) : (
                    <Empty>No description yet</Empty>
                  )}
                </Field>

                <Field label="Status" htmlFor="brand-active">
                  {editing && draft ? (
                    <div className="flex items-center gap-2">
                      <Switch
                        id="brand-active"
                        checked={draft.is_active}
                        onCheckedChange={(value) => setDraft({ ...draft, is_active: value })}
                      />
                      <span>{draft.is_active ? 'Active' : 'Inactive'}</span>
                    </div>
                  ) : (
                    <Badge variant={brand.is_active ? 'success' : 'secondary'}>
                      {brand.is_active ? 'Active' : 'Inactive'}
                    </Badge>
                  )}
                </Field>

                <Field label="Flows to purchasing" htmlFor="brand-flows-to-purchasing">
                  {editing && draft ? (
                    <Switch
                      id="brand-flows-to-purchasing"
                      checked={draft.flows_to_purchasing}
                      onCheckedChange={(value) =>
                        setDraft({ ...draft, flows_to_purchasing: value })
                      }
                    />
                  ) : brand.flows_to_purchasing ? (
                    'Yes'
                  ) : (
                    'No'
                  )}
                </Field>

                <Field label="Chatbot brand weight" htmlFor="brand-chatbot-weight">
                  {editing && draft ? (
                    <div className="grid gap-1.5">
                      <NumberInput
                        id="brand-chatbot-weight"
                        min={0}
                        max={9999}
                        step={0.1}
                        inputMode="decimal"
                        className="w-32"
                        value={draft.chatbot_weight}
                        onChange={(e) => setDraft({ ...draft, chatbot_weight: e.target.value })}
                        error={weightError}
                      />
                    </div>
                  ) : (
                    String(brand.chatbot_weight ?? 0)
                  )}
                </Field>

                <Field label="Customers can ask for this brand" htmlFor="brand-is-searchable">
                  {editing && draft ? (
                    <Switch
                      id="brand-is-searchable"
                      checked={draft.is_searchable}
                      onCheckedChange={(value) => setDraft({ ...draft, is_searchable: value })}
                    />
                  ) : (brand.is_searchable ?? true) ? (
                    'Yes'
                  ) : (
                    'No'
                  )}
                </Field>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>Access</CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                <Field label="Access levels">
                  {editing && draft ? (
                    accessTypeOptions.length === 0 ? (
                      <Empty>No access types are configured yet</Empty>
                    ) : (
                      <div className="flex flex-wrap gap-3">
                        {accessTypeOptions.map((opt) => (
                          <label
                            key={opt.code}
                            className="flex items-center gap-2 text-sm font-normal"
                          >
                            <Checkbox
                              checked={draft.access_levels.includes(opt.code)}
                              onCheckedChange={(value) => {
                                const next = new Set(draft.access_levels);
                                if (value) next.add(opt.code);
                                else next.delete(opt.code);
                                setDraft({ ...draft, access_levels: Array.from(next) });
                              }}
                            />
                            {opt.name || opt.code}
                          </label>
                        ))}
                      </div>
                    )
                  ) : accessLevelNames.length > 0 ? (
                    <div className="flex flex-wrap gap-1.5">
                      {accessLevelNames.map((name) => (
                        <Badge key={name} variant="secondary">
                          {name}
                        </Badge>
                      ))}
                    </div>
                  ) : (
                    <Empty>Open to everyone</Empty>
                  )}
                </Field>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>Record</CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                <Field label="Created">{formatDate(new Date(brand.created_at))}</Field>
                <Field label="Last updated">
                  {brand.updated_at ? formatDate(new Date(brand.updated_at)) : <Empty />}
                </Field>
                <Field label="Products in this brand">
                  {brand.product_count != null ? (
                    brand.product_count
                  ) : (
                    <Empty>Not counted</Empty>
                  )}
                </Field>
              </CardContent>
            </Card>
          </div>
        </div>
      </Container>
    </>
  );
}
