/**
 * ============================================================================
 * API CONTRACT - the editable spec table (PR 2) and extraction from text (PR 4)
 * ============================================================================
 *
 * Written out here because the frontend was built against it before any of it
 * existed (Phase 1), and because milestone 2's supplier portal renders the same
 * components against a different principal and has to match it.
 *
 * READS
 *
 *   GET /api/v1/master-data/product-specifications/by-product/{productId}
 *     -> ProductSpecDetail. `spec.values` is the table's row set; `spec.provenance`
 *        stamps each row with its source. A removed key appears ONLY in
 *        `provenance`, as `{source: 'human', absent: true}` with no entry in
 *        `values` - that tombstone is what stops re-derivation refilling it, and
 *        the table deliberately renders no row for it (removed means gone). The
 *        add picker offers it again; setting a value replaces the stamp.
 *     -> `exceptions[]` carries `reason: 'human_override_conflict'` with
 *        `proposed` = what the rules now read. Answered by SETTING THE VALUE;
 *        there is no resolve endpoint and there is not meant to be one (D9).
 *     -> `verification` is the VerificationBlock for the code (state, who vouched for
 *        it and when, and the was/now diff when its values moved under the stamp), and
 *        `values_hash` is the hash of the values on screen. Both ride this response so
 *        the Specifications tab needs no second round trip and both company copies of a
 *        code read the same badge (AC-D.14). The hash is echoed back on verify, which
 *        is what makes the same-transaction guard apply from the tab (AC-D.4). The
 *        verify / unverify routes themselves live in
 *        `spec-verification/services/specVerificationService.ts`.
 *
 *   GET /api/v1/master-data/spec-registry
 *     -> { keys: SpecRegistryKey[] }, vocabulary already merged.
 *
 *   GET /api/v1/master-data/spec-registry/applicable-keys?code={productCode}
 *     -> { code, keys: [{ spec_key, label, data_type, unit, allowed_values,
 *          synonyms, applicable, held }] }
 *        `applicable` mirrors derivation's `applies_when` gate; `held` is "has a
 *        value" and nothing else, so a REMOVED key is reported not held - its
 *        tombstone stays in `provenance` so re-derivation will not refill it, but
 *        the add picker offers it again and setting a value replaces the stamp.
 *        The code is matched case-insensitively. 404 on an unknown code -
 *        deliberately not an empty list, which would read as "this product may
 *        carry nothing".
 *
 *   GET /api/v1/master-data/spec-registry/similar?label={label}
 *     -> { label, match: { spec_key, label, matched_on, matched_text } | null }
 *        `matched_on` is one of spec_key | label | synonym.
 *
 * WRITES
 *
 *   PUT    .../by-product/{productId}/values/{specKey}   body { value }
 *   DELETE .../by-product/{productId}/values/{specKey}?mode=absent|revert
 *     `absent` = "this product does not have this spec", survives re-derivation.
 *     `revert` = hand the key back to the rules.
 *
 *   POST  /api/v1/master-data/spec-registry                   (create a key)
 *   POST  /api/v1/master-data/spec-registry/{specKey}/values  body { value }
 *     ONE word, appended server-side under a row lock. The PATCH below REPLACES
 *     `user_values`, so sending a list rebuilt from a cached read deletes whatever
 *     somebody else added in between; this route never takes the list from a client.
 *   PATCH /api/v1/master-data/spec-registry/{specKey}         (the registry editor,
 *     which shows the whole list and submits the whole list). `max_value` rides this
 *     PATCH too (AC-A.5, C.5) - null/absent clears the cap.
 *
 * TRY IT AND PREVIEW (AC-B.1, B.2)
 *
 *   POST /api/v1/master-data/spec-registry/{specKey}/try
 *     body { productId?: string, text?: string, rules: SpecDerivationRule[] }
 *       (exactly one of productId/text; `rules` is the DRAFT list, unsaved)
 *     -> { description: string, reads: [{ index, value, evidence }], winner_index }
 *        One `reads` entry per rule, same order; `evidence` is the exact text a match
 *        was read from, null when the row read nothing. `winner_index` is the first row
 *        with a value - what the engine would actually keep - or null when none matched.
 *        404 unknown product, 422 malformed rule (names the row index).
 *        Requires `master_data.spec_registry.view`.
 *
 *   POST /api/v1/master-data/spec-registry/{specKey}/preview   body { rules: SpecDerivationRule[] }
 *     -> { jobId: string }
 *        Starts an in-process background job (no worker, no queue - the same
 *        mechanism `reread-catalogue` already runs on) that derives the key for
 *        every active product with the draft rules. Requires
 *        `master_data.spec_registry.edit`. 409 `spec_preview_running` while another
 *        preview is already in flight, the running job's id in `detail`.
 *   GET  /api/v1/master-data/spec-registry/{specKey}/preview/{jobId}
 *     -> { status: 'pending' } | { status: 'done', changed, added, removed, unchanged,
 *          sample: [{ code, before, after }] (20) } | { status: 'failed', error }
 *        Hand-set values are never counted as changed - they are not derived. 404 an
 *        unknown jobId, or a jobId that belongs to a DIFFERENT specKey. Requires
 *        `master_data.spec_registry.edit` - the same grant that started the job.
 *
 * THE 422 THAT IS NOT AN ERROR ENVELOPE
 *
 * The writes above refuse a near-duplicate with a TOP-LEVEL body, not the
 * AppException envelope, because the client has to render WHICH existing thing it
 * collided with:
 *
 *     422 { error: string, match: {...} }
 *
 * `POST .../{specKey}/values` refuses one more case the same way: a word an
 * administrator SUPPRESSED on the key comes back as `match.matched_on:
 * 'suppressed_value'`, since it is absent from the merged vocabulary and adding it
 * here would overturn a decision made on the key itself.
 *
 * There is no way past it: a colliding word is refused, full stop. `extractApiError`
 * cannot read this body - it is string-only - so these calls parse it themselves and
 * throw a `SpecSimilarError` carrying the sentence that names the collision.
 *
 * EXTRACTION FROM PASTED TEXT (PR 4)
 *
 *   POST .../by-product/{productId}/extract              body { text: string }
 *     -> { product_code: string,
 *          engine: 'semantic' | 'deterministic',
 *          model: string | null,
 *          proposals: SpecProposal[],
 *          unchanged: number }
 *        Gated on `master_data.products.edit`, not on any spec_registry grant.
 *        WRITES NO SPECIFICATION DATA (AC-B.1, B.2): the pasted text reaches the
 *        request body and goes no further, so the flyer row count and the spec
 *        `updated_at` are unchanged across a call. There is no "save the text"
 *        anywhere. The one row a call can write is an AI usage-log row, and only when
 *        a model answered - the same bookkeeping every other model call books against
 *        itself, telemetry about the call rather than a claim about the product.
 *        `kind` on each proposal is decided SERVER-SIDE (AC-B.3), never here, because
 *        milestone 2's supplier review reads the same field off a different endpoint
 *        and two copies of the rule would drift: stored key absent -> `new`; stored
 *        equal after coercion -> OMITTED entirely and counted in `unchanged`; stored
 *        authored or tombstoned -> `conflict`; stored non-authored and different ->
 *        `change`, except a size key (the description-first set), which is a
 *        `conflict` on the key alone - provenance does not record which text a stored
 *        value was read from. One proposal per key.
 *        `engine: 'deterministic'` means no model was reachable and the rules alone
 *        read it - a 200 marked as such, never a 502 (AC-B.5).
 *        422 { detail } with a readable message when the text is blank or longer than
 *        8,000 characters. Never truncated.
 *
 *   POST .../by-product/{productId}/values/batch
 *     body { entries: [{ spec_key: string, value: SpecProposalValue,
 *                        unit?: string | null, evidence: string }] }
 *     -> { product_code: string, rows_written: number, spec_keys: string[] }
 *        `value` is the proposal's value UNCHANGED, a list included: a multi-value key
 *        (a two-tone finish) is coerced element-wise and stored as the list, so it
 *        stores what a re-derivation of the same words would.
 *        1..50 entries; an empty list is a 422. So is a `spec_key` over 100 characters,
 *        an `evidence` over 500, and the same `spec_key` twice in one batch - one key
 *        twice is two claims about one thing, and the second would silently win.
 *        ONE call through `apply_spec_values`
 *        with `source: 'human'` and evidence `read from text: <evidence>` (AC-B.9):
 *        N per-key calls would produce N fan-outs, N rendered-text rebuilds and N
 *        verification diffs for one user action. Atomic - one bad entry fails the
 *        whole batch through the choke point's own validation, so the table is never
 *        left half-applied.
 *
 * RETIRED WITH PR 4
 *
 * `PUT .../by-product/{id}/flyer-text`, the `flyer_text` field on the by-product
 * response and the four `/findability/*` routes are gone, together, in the deploy
 * that promotes flyer provenance to authored (AC-B.12, B.14). The flyer stops being
 * something derivation reads; it reaches the specs only as proposals a person accepts
 * through the two routes above.
 *
 * ============================================================================
 */
import { apiFetch } from '@/lib/api';
import { extractApiError } from '@/lib/api-client';
import type { SimilarKeyMatch, SpecDataType } from '@/components/spec-table';
import type {
  SpecProposal,
  SpecProposalValue,
} from '@/components/spec-proposals';
import type { SearchableSelectOption } from '@/components/common/SearchableSelect';
import type {
  SpecDerivationRule,
  SpecPreviewJobResult,
  SpecSearchPolicyRow,
  SpecTryResult,
  ProductSpecDetail,
  SpecPreviewResult,
  SpecRegistryKey,
  SpecRegistryKeyUpdateResult,
} from '../types/productSpec.types';
import { codeWithName } from '@/lib/product-display';

/**
 * One product's derived specs, or the reason there are none. Used by the
 * Specifications tab on the product record.
 */
export async function getProductSpecDetail(
  productId: string,
): Promise<ProductSpecDetail> {
  const response = await apiFetch(
    `/api/v1/master-data/product-specifications/by-product/${productId}`,
  );
  if (!response.ok) {
    throw new Error(
      await extractApiError(response, 'Failed to load derived specifications'),
    );
  }
  return response.json();
}

export async function getSpecRegistry(): Promise<{ keys: SpecRegistryKey[] }> {
  const response = await apiFetch('/api/v1/master-data/spec-registry');
  if (!response.ok) {
    throw new Error(
      await extractApiError(response, 'Failed to load the spec registry'),
    );
  }
  return response.json();
}

/** What the chatbot's parser picked from one message, one row per pick. */
export interface ParserPick {
  /** The customer's own words for it. */
  raw: string;
  kind: string;
  /** The specification it was read as; null for a product, category or list item. */
  field_label: string | null;
  value: string;
}

/**
 * Run the chatbot's real parser on one message. A dry run: nothing is sent, nothing is
 * saved. A refusal (parser unavailable) arrives as the server's own sentence.
 */
export async function tryInChatbot(
  text: string,
): Promise<{ prompt_version: number | null; picks: ParserPick[] }> {
  const response = await apiFetch('/api/v1/system/chatbot/parser-try', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text }),
  });
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'The chatbot reader is not available'));
  }
  return response.json();
}

/**
 * Run the ranker exactly as the chatbot would. Returns each candidate's score and the
 * keys it matched on, so a reviewer can see why a result placed where it did.
 */
export async function previewSpecSearch(body: {
  specs: { key: string; value: string | number }[];
  free_terms: string[];
  floor?: number;
  /** The raw sentence, read semantically. */
  phrase?: string;
  /** False to see the literal reading alone, for comparison. */
  understand?: boolean;
}): Promise<SpecPreviewResult> {
  const response = await apiFetch(
    '/api/v1/master-data/product-specifications/preview-search',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    },
  );
  if (!response.ok) {
    throw new Error(
      await extractApiError(response, 'Spec search preview failed'),
    );
  }
  return response.json();
}

/** One key as the add-a-specification picker needs it: may it, and does it already. */
export interface ApplicableSpecKey {
  spec_key: string;
  label: string;
  data_type: string;
  unit: string | null;
  allowed_values: string[];
  synonyms: Record<string, string[]>;
  /** How each value reads on screen (#423), keyed by the stored slug. */
  value_labels?: Record<string, string>;
  /** The `applies_when` gate, evaluated the way derivation evaluates it. */
  applicable: boolean;
  /** Already on the product, meaning it has a value. A removed key is reported not held. */
  held: boolean;
}

/**
 * Which keys this product MAY carry, and which it already does.
 *
 * Not `getKeysForProduct`, which answers from the values the product already holds -
 * the numerator where the picker needs the denominator.
 */
export async function getApplicableSpecKeys(
  productCode: string,
): Promise<{ code: string; keys: ApplicableSpecKey[] }> {
  const response = await apiFetch(
    `/api/v1/master-data/spec-registry/applicable-keys?code=${encodeURIComponent(productCode)}`,
  );
  if (!response.ok) {
    throw new Error(
      await extractApiError(response, 'Failed to load the specifications'),
    );
  }
  return response.json();
}

/** The existing key a proposed label already means, or null when it is genuinely new. */
export async function getSimilarSpecKey(
  label: string,
): Promise<SimilarKeyMatch | null> {
  const response = await apiFetch(
    `/api/v1/master-data/spec-registry/similar?label=${encodeURIComponent(label)}`,
  );
  if (!response.ok) {
    throw new Error(
      await extractApiError(response, 'Failed to check the name'),
    );
  }
  return (await response.json()).match ?? null;
}

/**
 * The server refused a near-duplicate, in the sentence that names the collision.
 *
 * Its own error type because the refusal is a product answer rather than a failure -
 * "that word is already black on Finish" - and the caller distinguishes it from a
 * network or permission error. There is no way past it.
 */
export class SpecSimilarError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'SpecSimilarError';
  }
}

/**
 * A new word that another specification already owns (409
 * `spec_registry_word_on_other_key`). Carries which word, and the value it was added
 * under, so the words grid can put the sentence under that row.
 */
export class SpecWordRefusedError extends Error {
  constructor(
    message: string,
    readonly word: string,
    readonly value: string | null,
  ) {
    super(message);
    this.name = 'SpecWordRefusedError';
  }
}

/**
 * Raise the near-duplicate refusal as itself, and anything else as a plain error.
 *
 * `extractApiError` is still what handles every ordinary failure below; this reads the
 * body first ONLY for the 422 shape it cannot represent, because the sentence naming
 * the collision lives in `error` rather than in `detail`.
 */
async function throwSpecWriteError(
  response: Response,
  fallback: string,
): Promise<never> {
  if (response.status === 422) {
    let body: { error?: string; match?: Record<string, unknown> } | null = null;
    try {
      body = await response.clone().json();
    } catch {
      body = null;
    }
    if (body?.match) {
      throw new SpecSimilarError(String(body.error ?? fallback));
    }
  }
  if (response.status === 409) {
    let body: { code?: string; details?: { word?: unknown; value?: unknown } } | null = null;
    try {
      body = await response.clone().json();
    } catch {
      body = null;
    }
    const details = body?.details;
    if (body?.code === 'spec_registry_word_on_other_key' && typeof details?.word === 'string') {
      throw new SpecWordRefusedError(
        await extractApiError(response, fallback),
        details.word,
        typeof details.value === 'string' ? details.value : null,
      );
    }
  }
  throw new Error(await extractApiError(response, fallback));
}

/** Register a new spec key. Owned by whoever creates it - never seed-repaired. */
export async function createSpecKey(body: {
  spec_key: string;
  label: string;
  data_type: SpecDataType | string;
  unit?: string | null;
  allowed_values?: string[];
  user_synonyms?: Record<string, string[]>;
  rank_weight?: number;
  is_active?: boolean;
}): Promise<SpecRegistryKey> {
  const response = await apiFetch('/api/v1/master-data/spec-registry', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    await throwSpecWriteError(response, 'Failed to create the spec key');
  }
  return response.json();
}

/**
 * Add ONE word to a key's vocabulary, from the product page.
 *
 * A separate call rather than `updateSpecKey` with one field, for two reasons. The
 * permissions differ: this is `master_data.products.edit` (a merchandiser correcting a
 * spec), while everything else on the PATCH route needs `master_data.spec_registry.edit`.
 * And the semantics differ: PATCH REPLACES the list, so a caller sending a list it
 * rebuilt from an earlier read silently deletes any word added in between. The word
 * travels alone and the server appends it.
 */
export async function addValueToSpecKey(
  specKey: string,
  value: string,
): Promise<SpecRegistryKey> {
  const response = await apiFetch(
    `/api/v1/master-data/spec-registry/${specKey}/values`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ value }),
    },
  );
  if (!response.ok) {
    await throwSpecWriteError(response, 'Failed to add the value');
  }
  return response.json();
}

/**
 * Edit calibration and extend vocabulary. A seeded key's `allowed_values` are
 * rejected by the API on purpose - they are the chatbot parser's contract.
 */
export async function updateSpecKey(
  specKey: string,
  body: {
    label?: string;
    /** What the field means to the chatbot. null clears it. */
    parser_hint?: string | null;
    rank_weight?: number;
    is_active?: boolean;
    match_tolerance?: number;
    match_decay?: number;
    user_synonyms?: Record<string, string[]>;
    suppressed_synonyms?: Record<string, string[]>;
    allowed_values?: string[];
    excluded_values?: string[];
    value_weights?: Record<string, number>;
    user_values?: string[];
    suppressed_values?: string[];
    derivation_rules?: SpecDerivationRule[];
    applies_when?: Record<string, string[]>;
    /** The plausibility cap (AC-A.5, C.5). null clears it; absent leaves it as stored. */
    max_value?: number | null;
    /** How each value reads on screen (#423, folded into the spec workbench
     * redesign). Trimmed and validated server-side; an empty label drops the key. */
    value_labels?: Record<string, string>;
  },
): Promise<SpecRegistryKeyUpdateResult> {
  const response = await apiFetch(
    `/api/v1/master-data/spec-registry/${specKey}`,
    {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    },
  );
  if (!response.ok) {
    await throwSpecWriteError(response, 'Failed to save the spec key');
  }
  return response.json();
}

/** One spec a given product carries, and where it was read from. */
export interface ProductSpecKey {
  value: string | number | boolean | null;
  source: string | null;
}

/**
 * Which spec keys a product code carries, so the keys table can be filtered by product.
 * `matched_product` is null when the code names nothing.
 */
export async function getKeysForProduct(code: string): Promise<{
  code: string;
  matched_product: { id: string; product_code: string } | null;
  keys: Record<string, ProductSpecKey>;
}> {
  const response = await apiFetch(
    `/api/v1/master-data/spec-registry/keys-for-product?code=${encodeURIComponent(code)}`,
  );
  if (!response.ok) {
    throw new Error(
      await extractApiError(response, 'Failed to look up the product'),
    );
  }
  return response.json();
}

/** Every product carrying a key, with the words each value was read from. */
export interface SpecKeyProduct {
  id: string;
  product_code: string;
  description: string | null;
  class: string | null;
  value: string | number | boolean | null;
  source: string | null;
  evidence: string | null;
}

export interface SpecKeyProducts {
  spec_key: string;
  label: string;
  total: number;
  by_value: { value: string | null; count: number }[];
  by_class: { class: string | null; count: number }[];
  by_source: { source: string | null; count: number }[];
  products: SpecKeyProduct[];
}

/** How many products carry each key right now (not `measured_coverage`), and when
 *  a product carrying it was last read - what the record header shows. */
export interface SpecCoverage {
  coverage: Record<string, number>;
  /** spec_key -> ISO time of the newest read among the products carrying it. */
  last_read: Record<string, string>;
}

export async function getSpecCoverage(): Promise<SpecCoverage> {
  const response = await apiFetch('/api/v1/master-data/spec-registry/coverage');
  if (!response.ok) {
    throw new Error(
      await extractApiError(response, 'Failed to load the coverage'),
    );
  }
  return response.json();
}

export async function getSpecKeyProducts(
  specKey: string,
  params: {
    value?: string;
    q?: string;
    classLabel?: string;
    source?: string;
    limit?: number;
    offset?: number;
  } = {},
): Promise<SpecKeyProducts> {
  const query = new URLSearchParams();
  if (params.value !== undefined) query.set('value', params.value);
  // Searched server-side over the whole key, not over the page on screen.
  if (params.q) query.set('q', params.q);
  // Class and Source narrow the query itself (UAC B.5, D17), not the page already on
  // screen - a key with 10,000 rows only has one page loaded client-side.
  if (params.classLabel) query.set('class_label', params.classLabel);
  if (params.source) query.set('source', params.source);
  query.set('limit', String(params.limit ?? 100));
  query.set('offset', String(params.offset ?? 0));
  const response = await apiFetch(
    `/api/v1/master-data/spec-registry/${specKey}/products?${query.toString()}`,
  );
  if (!response.ok) {
    throw new Error(
      await extractApiError(response, 'Failed to load the products'),
    );
  }
  return response.json();
}

/** Read one product again with the rules that are live now. */
export async function rederiveProduct(
  productId: string,
): Promise<{ written: number }> {
  const response = await apiFetch(
    `/api/v1/master-data/product-specifications/by-product/${productId}/rederive`,
    { method: 'POST' },
  );
  if (!response.ok) {
    throw new Error(
      await extractApiError(response, 'Could not read this product again'),
    );
  }
  return response.json();
}

/** Set a value the catalogue never states. Held against every later re-derivation. */
export async function setSpecValueByHand(
  productId: string,
  specKey: string,
  value: string | number | boolean,
): Promise<void> {
  const response = await apiFetch(
    `/api/v1/master-data/product-specifications/by-product/${productId}/values/${specKey}`,
    {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ value }),
    },
  );
  if (!response.ok) {
    throw new Error(
      await extractApiError(response, 'Could not save the value'),
    );
  }
}

/**
 * Take a value away, one of the two ways, because they mean different things.
 *
 * `revert` hands the key back to derivation and it comes back with whatever the
 * catalogue says. `absent` is a statement of fact - this product does not have this
 * spec - and it survives every later run as a tombstone rather than being filled in
 * again. `revert` is the default because it is what the shipped screen always did.
 */
export async function clearSpecValueByHand(
  productId: string,
  specKey: string,
  mode: 'revert' | 'absent' = 'revert',
): Promise<void> {
  const response = await apiFetch(
    `/api/v1/master-data/product-specifications/by-product/${productId}/values/${specKey}?mode=${mode}`,
    { method: 'DELETE' },
  );
  if (!response.ok) {
    throw new Error(
      await extractApiError(response, 'Could not clear the value'),
    );
  }
}

export async function deleteSpecKey(specKey: string): Promise<void> {
  const response = await apiFetch(
    `/api/v1/master-data/spec-registry/${specKey}`,
    {
      method: 'DELETE',
    },
  );
  if (!response.ok) {
    throw new Error(
      await extractApiError(response, 'Failed to delete the spec key'),
    );
  }
}

/** Every scoring knob the ranker reads, with its shipped default alongside. */
export async function getSearchPolicy(): Promise<{
  policy: SpecSearchPolicyRow[];
}> {
  const response = await apiFetch('/api/v1/master-data/spec-registry/policy');
  if (!response.ok) {
    throw new Error(
      await extractApiError(response, 'Failed to load the search settings'),
    );
  }
  return response.json();
}

export async function updateSearchPolicy(
  policyKey: string,
  value: number,
): Promise<{ policy_key: string; value: number }> {
  const response = await apiFetch(
    `/api/v1/master-data/spec-registry/policy/${policyKey}`,
    {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ value }),
    },
  );
  if (!response.ok) {
    throw new Error(
      await extractApiError(response, 'Failed to save the search setting'),
    );
  }
  return response.json();
}

export interface CatalogueStatus {
  status: 'idle' | 'running' | 'done' | 'failed';
  started_at: string | null;
  finished_at: string | null;
  result: { codes?: number; written?: number; error?: string } | null;
  /** The rules have been edited since the stored specifications were read. */
  rules_changed_since_last_read: boolean;
  ever_read: boolean;
}

export async function getCatalogueStatus(): Promise<CatalogueStatus> {
  const response = await apiFetch(
    '/api/v1/master-data/spec-registry/catalogue-status',
  );
  if (!response.ok) {
    throw new Error(
      await extractApiError(response, 'Failed to read the catalogue status'),
    );
  }
  return response.json();
}

/** Re-read every product with the rules as they stand now. Runs in the background. */
export async function rereadCatalogue(): Promise<{ status: string }> {
  const response = await apiFetch(
    '/api/v1/master-data/spec-registry/reread-catalogue',
    {
      method: 'POST',
    },
  );
  if (!response.ok) {
    throw new Error(
      await extractApiError(response, 'Failed to start reading the catalogue'),
    );
  }
  return response.json();
}

// --- Extraction from pasted text (PR 4) --------------------------------------------

/** What one extract call answers. The banner at the top of this file is the contract. */
export interface SpecExtractionResult {
  product_code: string;
  /** `deterministic` means no model was reachable and the rules alone read the text. */
  engine: 'semantic' | 'deterministic';
  model: string | null;
  proposals: SpecProposal[];
  /** Keys the text merely restated. Counted rather than listed, on purpose. */
  unchanged: number;
}

/** One accepted proposal, as the batch write takes it. */
export interface SpecProposalEntry {
  spec_key: string;
  /**
   * The proposed value, unchanged. A LIST where the key holds more than one value (a
   * two-tone finish): the batch route coerces it element-wise and stores the list, so
   * flattening it here would store something a re-derivation of the same words would
   * not.
   */
  value: SpecProposalValue;
  unit?: string | null;
  /** The words it was read from. Stored as the value's evidence. */
  evidence: string;
}

export interface SpecBatchApplyResult {
  product_code: string;
  rows_written: number;
  spec_keys: string[];
}

/**
 * Read a pasted text onto this product's specifications, and propose - never write.
 *
 * The route writes nothing: the text reaches the request body and goes no further, so
 * the panel can be pressed as often as the user likes without touching the stored
 * specifications. Nothing is saved until `applySpecProposals` below.
 *
 * A blank or over-long text comes back as an ordinary 422 message rather than the
 * near-duplicate shape the registry writes use, so `extractApiError` reads it: the
 * sentence it returns is what the panel shows above the box, with the text still in it.
 */
export async function extractSpecProposals(
  productId: string,
  text: string,
): Promise<SpecExtractionResult> {
  const response = await apiFetch(
    `/api/v1/master-data/product-specifications/by-product/${productId}/extract`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    },
  );
  if (!response.ok) {
    throw new Error(
      await extractApiError(response, 'Could not read this text'),
    );
  }
  return response.json();
}

/**
 * Write the accepted proposals as authored values, in ONE call.
 *
 * One call and not one per key: N would produce N fan-outs, N rendered-text rebuilds
 * and N verification diffs for a single user action, and a partial failure would leave
 * the table half-applied. The server refuses the whole batch when any entry is bad,
 * which is why the caller can refetch once and trust what it reads.
 */
// --- Try it and preview (AC-B.1, B.2) -------------------------------------------

export type TrySpecRulesSource =
  | { productId: string; text?: undefined }
  | { text: string; productId?: undefined };

/** Try the DRAFT rules against a real product or a pasted text, unsaved. */
export async function trySpecRules(
  specKey: string,
  body: { rules: SpecDerivationRule[] } & TrySpecRulesSource,
): Promise<SpecTryResult> {
  const response = await apiFetch(
    `/api/v1/master-data/spec-registry/${specKey}/try`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        rules: body.rules,
        productId: body.productId,
        text: body.text,
      }),
    },
  );
  if (!response.ok) {
    throw new Error(
      await extractApiError(response, 'Could not try these rules'),
    );
  }
  return response.json();
}

/** Start an in-process background job (no worker, no queue) that derives the key for
 *  every active product with the draft rules. Poll `getSpecPreview` for the result.
 *  409 while another preview is already running - `extractApiError` reads that same
 *  as any other failure, so the caller sees the sentence naming it, not a raw code. */
export async function previewSpecRules(
  specKey: string,
  body: { rules: SpecDerivationRule[] },
): Promise<{ jobId: string }> {
  const response = await apiFetch(
    `/api/v1/master-data/spec-registry/${specKey}/preview`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    },
  );
  if (!response.ok) {
    throw new Error(
      await extractApiError(response, 'Could not start the preview'),
    );
  }
  return response.json();
}

export async function getSpecPreview(
  specKey: string,
  jobId: string,
): Promise<SpecPreviewJobResult> {
  const response = await apiFetch(
    `/api/v1/master-data/spec-registry/${specKey}/preview/${jobId}`,
  );
  if (!response.ok) {
    throw new Error(
      await extractApiError(response, 'Could not read the preview'),
    );
  }
  return response.json();
}

/**
 * The try-it product picker, in `fetchOptions` mode over the whole product master
 * (AC-B.3, R5 of flyer-code-adopt).
 */
export async function fetchProductPickerOptions(
  query: string,
  pageIndex: number,
): Promise<SearchableSelectOption[]> {
  const search = new URLSearchParams({
    limit: '50',
    offset: String(pageIndex * 50),
    ...(query ? { query } : {}),
  });
  const response = await apiFetch(
    `/api/v1/master-data/products/select?${search.toString()}`,
  );
  if (!response.ok) {
    throw new Error(
      await extractApiError(response, 'Could not search the products'),
    );
  }
  const body = await response.json();
  return (body.data ?? []).map(
    (p: { id: string; product_code: string; product_name: string }) => ({
      value: p.id,
      label: codeWithName(p.product_code, p.product_name),
      searchText: `${p.product_code} ${p.product_name}`,
    }),
  );
}

export async function applySpecProposals(
  productId: string,
  entries: SpecProposalEntry[],
): Promise<SpecBatchApplyResult> {
  const response = await apiFetch(
    `/api/v1/master-data/product-specifications/by-product/${productId}/values/batch`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ entries }),
    },
  );
  if (!response.ok) {
    throw new Error(
      await extractApiError(response, 'Could not save the specifications'),
    );
  }
  return response.json();
}
