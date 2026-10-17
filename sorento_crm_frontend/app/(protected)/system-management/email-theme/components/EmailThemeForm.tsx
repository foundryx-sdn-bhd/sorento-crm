'use client';

import { useEffect, useState } from 'react';
import { Container } from '@/components/common/container';
import { PageHeader } from '@/components/common/PageHeader';
import { SectionSkeleton } from '@/components/common/SectionSkeleton';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { NumberInput } from '@/components/ui/number-input';
import { numberFieldError } from '@/lib/number-field';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { useDebouncedValue } from '@/hooks/useDebouncedValue';
import EmailPreviewFrame from '../../email-templates/components/EmailPreviewFrame';
import { ColorField } from './ColorField';
import { SocialLinksField } from './SocialLinksField';
import { useEmailThemePreview, useEmailThemeQuery, useSaveEmailThemeMutation } from '../hooks/useEmailTheme';
import type { EmailThemeSampleCode } from '../types/emailTheme.types';
import { draftToPayload, toDraft, type ThemeDraft } from '../lib/themeDraft';

const LOGO_ALIGNMENT_OPTIONS = [
  { value: 'left', label: 'Left' },
  { value: 'center', label: 'Center' },
];

const HEADER_STYLE_OPTIONS = [
  { value: 'brand', label: 'Brand colour band' },
  { value: 'white', label: 'White with hairline' },
];

const SAMPLE_MAIL_OPTIONS: { value: EmailThemeSampleCode; label: string }[] = [
  { value: 'auth_password_reset', label: 'Password reset' },
  { value: 'user_invitation', label: 'User invitation' },
  { value: 'onboarding_intake_link', label: 'Onboarding intake link' },
  { value: 'purchase_request_approval_link', label: 'Purchase request approval link' },
];

const BUTTON_WIDTH_OPTIONS = [
  { value: 'auto', label: 'Auto' },
  { value: 'full', label: 'Full width' },
];

function labelFor(options: { value: string; label: string }[], value: string): string {
  return options.find((o) => o.value === value)?.label ?? value;
}

/**
 * The Email Theme page (AC-EM020/021/030, S7): a form (no subtitle, one
 * primary CTA in the header) over a live preview, two columns on desktop,
 * stacked at 375. A client component so the page module itself can stay a
 * server component and carry `metadata` (Next.js does not allow both in one
 * file).
 */
export default function EmailThemeForm() {
  const themeQuery = useEmailThemeQuery();
  const saveMut = useSaveEmailThemeMutation();
  const previewMut = useEmailThemePreview();
  const [draft, setDraft] = useState<ThemeDraft | null>(null);
  // Preview-only: never part of the draft, never sent on Save.
  const [sampleCode, setSampleCode] = useState<EmailThemeSampleCode>('auth_password_reset');

  // Seed the draft once the stored theme arrives; a later refetch (e.g. after
  // save) does not clobber what the admin is mid-typing.
  useEffect(() => {
    if (themeQuery.data && draft === null) {
      setDraft(toDraft(themeQuery.data.theme));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [themeQuery.data]);

  const defaults = themeQuery.data?.defaults;
  const fonts = themeQuery.data?.fonts ?? [];
  const fontOptions = fonts.map((f) => ({ value: f.value, label: f.label }));

  const debouncedDraft = useDebouncedValue(draft, 400);

  // Live preview (AC-EM030): the UNSAVED form values, redrawn ~400ms after the
  // last edit so a fast typist doesn't fire one request per keystroke.
  useEffect(() => {
    if (!debouncedDraft) return;
    previewMut.mutate({ theme: draftToPayload(debouncedDraft), code: sampleCode });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedDraft, sampleCode]);

  // Blank means "use the theme default", so only a typed value out of range is wrong.
  const sizeErrors = draft
    ? {
        logo_height: numberFieldError(draft.logo_height, { min: 20, max: 80, optional: true }),
        button_radius: numberFieldError(draft.button_radius, { min: 0, max: 32, optional: true }),
        card_radius: numberFieldError(draft.card_radius, { min: 0, max: 24, optional: true }),
      }
    : null;
  const [checked, setChecked] = useState(false);

  function patch(fields: Partial<ThemeDraft>) {
    setDraft((prev) => (prev ? { ...prev, ...fields } : prev));
  }

  async function handleSave() {
    if (!draft) return;
    if (sizeErrors && Object.values(sizeErrors).some(Boolean)) {
      setChecked(true);
      return;
    }
    try {
      const saved = await saveMut.mutateAsync(draftToPayload(draft));
      setDraft(toDraft(saved.theme));
    } catch {
      // toast handled by the mutation hook.
    }
  }

  const loading = themeQuery.isLoading || !draft || !defaults;

  return (
    <>
      <Container>
        <PageHeader
          title="Email Theme"
          actions={
            <Button onClick={handleSave} disabled={loading || saveMut.isPending}>
              {saveMut.isPending ? 'Saving…' : 'Save'}
            </Button>
          }
        />
      </Container>
      <Container>
        {loading ? (
          <SectionSkeleton rows={8} />
        ) : (
          <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,1fr)_420px]">
            <div className="min-w-0 space-y-6">
              <Card>
                <CardHeader>
                  <CardTitle>Brand</CardTitle>
                </CardHeader>
                <CardContent className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                  <div className="space-y-1 sm:col-span-2">
                    <Label htmlFor="theme-logo-url">Logo URL</Label>
                    <Input
                      id="theme-logo-url"
                      value={draft.logo_url}
                      onChange={(e) => patch({ logo_url: e.target.value })}
                      placeholder={defaults.logo_url}
                    />
                  </div>
                  <div className="space-y-1">
                    <Label htmlFor="theme-logo-alignment">Logo alignment</Label>
                    <SearchableSelect
                      id="theme-logo-alignment"
                      value={draft.logo_alignment}
                      onChange={(value) => patch({ logo_alignment: value })}
                      options={LOGO_ALIGNMENT_OPTIONS}
                      clearable
                      placeholder={`Default (${labelFor(LOGO_ALIGNMENT_OPTIONS, defaults.logo_alignment)})`}
                    />
                  </div>
                  <div className="space-y-1">
                    <Label htmlFor="theme-logo-height">Logo height (px)</Label>
                    <NumberInput
                      id="theme-logo-height"
                      min={20}
                      max={80}
                      value={draft.logo_height}
                      onChange={(e) => patch({ logo_height: e.target.value })}
                      placeholder={String(defaults.logo_height)}
                      error={checked ? sizeErrors?.logo_height : undefined}
                    />
                  </div>
                  <div className="space-y-1">
                    <Label htmlFor="theme-header-style">Header style</Label>
                    <SearchableSelect
                      id="theme-header-style"
                      value={draft.header_style}
                      onChange={(value) => patch({ header_style: value })}
                      options={HEADER_STYLE_OPTIONS}
                      clearable
                      placeholder={`Default (${labelFor(HEADER_STYLE_OPTIONS, defaults.header_style)})`}
                    />
                  </div>
                  <ColorField
                    id="theme-primary-color"
                    label="Primary colour"
                    value={draft.primary_color}
                    onChange={(value) => patch({ primary_color: value })}
                    defaultValue={defaults.primary_color}
                  />
                  <ColorField
                    id="theme-accent-color"
                    label="Accent colour"
                    value={draft.accent_color}
                    onChange={(value) => patch({ accent_color: value })}
                    defaultValue={defaults.accent_color}
                  />
                  <ColorField
                    id="theme-page-background"
                    label="Page background"
                    value={draft.page_background}
                    onChange={(value) => patch({ page_background: value })}
                    defaultValue={defaults.page_background}
                  />
                </CardContent>
              </Card>

              <Card>
                <CardHeader>
                  <CardTitle>Button</CardTitle>
                </CardHeader>
                <CardContent className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                  <ColorField
                    id="theme-button-color"
                    label="Fill colour"
                    value={draft.button_color}
                    onChange={(value) => patch({ button_color: value })}
                    defaultValue={defaults.button_color}
                  />
                  <ColorField
                    id="theme-button-text-color"
                    label="Text colour"
                    value={draft.button_text_color}
                    onChange={(value) => patch({ button_text_color: value })}
                    defaultValue={defaults.button_text_color}
                  />
                  <div className="space-y-1">
                    <Label htmlFor="theme-button-radius">Corner radius (px)</Label>
                    <NumberInput
                      id="theme-button-radius"
                      min={0}
                      max={32}
                      value={draft.button_radius}
                      onChange={(e) => patch({ button_radius: e.target.value })}
                      placeholder={String(defaults.button_radius)}
                      error={checked ? sizeErrors?.button_radius : undefined}
                    />
                  </div>
                  <div className="space-y-1">
                    <Label htmlFor="theme-button-width">Width</Label>
                    <SearchableSelect
                      id="theme-button-width"
                      value={draft.button_width}
                      onChange={(value) => patch({ button_width: value })}
                      options={BUTTON_WIDTH_OPTIONS}
                      clearable
                      placeholder={`Default (${labelFor(BUTTON_WIDTH_OPTIONS, defaults.button_width)})`}
                    />
                  </div>
                </CardContent>
              </Card>

              <Card>
                <CardHeader>
                  <CardTitle>Text</CardTitle>
                </CardHeader>
                <CardContent className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                  <div className="space-y-1">
                    <Label htmlFor="theme-font">Font</Label>
                    <SearchableSelect
                      id="theme-font"
                      value={draft.font}
                      onChange={(value) => patch({ font: value })}
                      options={fontOptions}
                      clearable
                      placeholder={`Default (${labelFor(fontOptions, defaults.font)})`}
                    />
                  </div>
                </CardContent>
              </Card>

              <Card>
                <CardHeader>
                  <CardTitle>Footer</CardTitle>
                </CardHeader>
                <CardContent className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                  <div className="space-y-1">
                    <Label htmlFor="theme-company-name">Company name</Label>
                    <Input
                      id="theme-company-name"
                      value={draft.company_name}
                      onChange={(e) => patch({ company_name: e.target.value })}
                      placeholder={defaults.company_name}
                    />
                  </div>
                  <div className="space-y-1">
                    <Label htmlFor="theme-help-email">Help email</Label>
                    <Input
                      id="theme-help-email"
                      type="email"
                      value={draft.help_email}
                      onChange={(e) => patch({ help_email: e.target.value })}
                      placeholder={defaults.help_email}
                    />
                  </div>
                  <div className="space-y-1">
                    <Label htmlFor="theme-help-url">Help URL</Label>
                    <Input
                      id="theme-help-url"
                      value={draft.help_url}
                      onChange={(e) => patch({ help_url: e.target.value })}
                      placeholder={defaults.help_url}
                    />
                  </div>
                  <div className="space-y-1 sm:col-span-2">
                    <Label htmlFor="theme-address">Address</Label>
                    <Textarea
                      id="theme-address"
                      value={draft.address}
                      onChange={(e) => patch({ address: e.target.value })}
                      placeholder={defaults.address}
                      rows={2}
                    />
                  </div>
                  <div className="space-y-1 sm:col-span-2">
                    <Label htmlFor="theme-footer-note">Why you received this</Label>
                    <Textarea
                      id="theme-footer-note"
                      value={draft.footer_note}
                      onChange={(e) => patch({ footer_note: e.target.value })}
                      placeholder={defaults.footer_note}
                      rows={2}
                    />
                  </div>
                  <div className="sm:col-span-2">
                    <SocialLinksField
                      value={draft.social_links}
                      onChange={(value) => patch({ social_links: value })}
                    />
                  </div>
                </CardContent>
              </Card>

              <Card>
                <CardHeader>
                  <CardTitle>Card</CardTitle>
                </CardHeader>
                <CardContent className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                  <div className="space-y-1">
                    <Label htmlFor="theme-card-radius">Corner radius (px)</Label>
                    <NumberInput
                      id="theme-card-radius"
                      min={0}
                      max={24}
                      value={draft.card_radius}
                      onChange={(e) => patch({ card_radius: e.target.value })}
                      placeholder={String(defaults.card_radius)}
                      error={checked ? sizeErrors?.card_radius : undefined}
                    />
                  </div>
                </CardContent>
              </Card>
            </div>

            <div className="lg:sticky lg:top-6 lg:h-fit">
              <Card>
                <CardHeader>
                  <CardTitle>Live preview</CardTitle>
                </CardHeader>
                <CardContent className="space-y-3">
                  <div className="space-y-1">
                    <Label htmlFor="theme-sample-mail">Sample mail</Label>
                    <SearchableSelect
                      id="theme-sample-mail"
                      value={sampleCode}
                      onChange={(value) => setSampleCode(value as EmailThemeSampleCode)}
                      options={SAMPLE_MAIL_OPTIONS}
                    />
                  </div>
                  <EmailPreviewFrame
                    subject={previewMut.data?.subject}
                    html={previewMut.data?.body_html}
                    isLoading={previewMut.isPending}
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
