export interface EmailTemplate {
  id: string;
  code: string;
  name: string;
  description: string | null;
  subject: string;
  preheader: string | null;
  body_html: string;
  body_text: string | null;
  layout_json: EmailLayoutDocument | null;
  is_active: boolean;
  is_system: boolean;
  created_by_user_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface EmailTemplateCreateBody {
  code: string;
  name: string;
  description?: string | null;
  subject: string;
  preheader?: string | null;
  body_html?: string;
  body_text?: string | null;
  layout_json?: EmailLayoutDocument | null;
  is_active?: boolean;
}

export interface EmailTemplateUpdateBody {
  code?: string;
  name?: string;
  description?: string | null;
  subject?: string;
  preheader?: string | null;
  body_html?: string;
  body_text?: string | null;
  layout_json?: EmailLayoutDocument | null;
  is_active?: boolean;
}

export interface EmailTemplatePreview {
  subject: string;
  body_html: string;
  body_text: string;
}

export interface PreviewDraftBody {
  subject: string;
  preheader?: string | null;
  layout_json?: EmailLayoutDocument | null;
  body_html?: string | null;
  body_text?: string | null;
  code?: string | null;
  context?: Record<string, unknown> | null;
}

export interface TemplateVariable {
  key: string;
  label: string;
  sample?: string | null;
}

export interface ListResponse<T> {
  data: T[];
  pagination: { total: number; page: number; limit: number };
  empty: boolean;
}

// --- Block document (the contract, PLAN-email-layout-28sep.md) -----------

export type BlockAlign = 'left' | 'center';

export interface BrandHeaderBlock {
  id?: string;
  type: 'brand_header';
}

export interface HeadingBlock {
  id?: string;
  type: 'heading';
  text: string;
  align: BlockAlign;
}

export interface IntroBlock {
  id?: string;
  type: 'intro';
  text: string;
  align: BlockAlign;
}

export interface FactsRow {
  label: string;
  value: string;
}

export interface FactsBlock {
  id?: string;
  type: 'facts';
  rows: FactsRow[];
  hide_empty: boolean;
}

export interface ButtonBlock {
  id?: string;
  type: 'button';
  label: string;
  url: string;
}

export interface LinkBlock {
  id?: string;
  type: 'link';
  label: string;
  url: string;
}

export interface CustomTextBlock {
  id?: string;
  type: 'custom_text';
  html: string;
}

export interface FooterBlock {
  id?: string;
  type: 'footer';
  note?: string | null;
}

export type EmailBlock =
  | BrandHeaderBlock
  | HeadingBlock
  | IntroBlock
  | FactsBlock
  | ButtonBlock
  | LinkBlock
  | CustomTextBlock
  | FooterBlock;

export type EmailBlockType = EmailBlock['type'];

/**
 * The card width the shell renders the document at (EMAIL-HANDOVER-QTY): `standard` is
 * the 600px card every mail ships on, `wide` the 900px one for table emails, `print`
 * the 660px one for a table email that gets printed on A4 portrait, such as the order
 * inquiry handover (OI-EMAIL-PRINT). A stored document without the key is standard.
 */
export type EmailLayoutWidth = 'standard' | 'wide' | 'print';

export interface EmailLayoutDocument {
  version: 1;
  blocks: EmailBlock[];
  width?: EmailLayoutWidth;
}

/**
 * A patch that can carry any single block type's own fields, all optional -
 * `Partial<EmailBlock>` (a union) only keeps the keys every variant shares
 * (`id`, `type`), which is too narrow for a settings editor that patches one
 * concrete block at a time. Every block-settings component patches through
 * this shape instead.
 */
export type AnyBlockPatch = Partial<BrandHeaderBlock> &
  Partial<HeadingBlock> &
  Partial<IntroBlock> &
  Partial<FactsBlock> &
  Partial<ButtonBlock> &
  Partial<LinkBlock> &
  Partial<CustomTextBlock> &
  Partial<FooterBlock>;
