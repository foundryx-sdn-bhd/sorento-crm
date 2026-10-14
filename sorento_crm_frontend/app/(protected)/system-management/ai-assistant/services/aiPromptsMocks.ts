/**
 * Phase-1 prototype mock fixtures for the prompt registry. Swapped out in
 * Phase 2 by flipping `USE_PROMPT_MOCKS` in aiPromptsService.ts. Kept as a
 * module (not inline) so vitest can import the same fixtures.
 */
import type {
  DryRunPayload,
  DryRunResponse,
  PromptKeySummary,
  PromptVersionDetail,
  PromptVersionsResponse,
  SaveVersionError,
  SaveVersionPayload,
  SetLabelPayload,
  SetLabelResponse,
} from './aiPromptsService';

const NOW = '2026-07-03T09:00:00';

export const MOCK_PROMPT_KEYS: PromptKeySummary[] = [
  {
    name: 'reformulator',
    role: 'Rewrite turn → standalone query',
    active: true,
    dry_runnable: true,
    activates_in: null,
    variables: ['current_date'],
    production_version: 1,
    staging_version: null,
    latest_version: 1,
    updated_at: NOW,
    updated_by_name: 'System (seed)',
    provider: null,
    model: null,
    reasoning_effort: null,
    supports_reasoning_effort: false,
  },
  {
    name: 'router',
    role: 'Intent / routing (is-record-Q? how-to? handoff?)',
    active: true,
    dry_runnable: true,
    activates_in: null,
    variables: [],
    production_version: 2,
    staging_version: 2,
    latest_version: 2,
    updated_at: NOW,
    updated_by_name: 'Jayson',
    provider: null,
    model: null,
    reasoning_effort: null,
    supports_reasoning_effort: false,
  },
  {
    name: 'agent_system',
    role: 'Thinker / executor ReAct core',
    active: true,
    dry_runnable: true,
    activates_in: null,
    variables: [],
    production_version: 1,
    staging_version: null,
    latest_version: 1,
    updated_at: NOW,
    updated_by_name: 'System (seed)',
    provider: null,
    model: null,
    reasoning_effort: null,
    supports_reasoning_effort: false,
  },
  {
    name: 'synthesizer',
    role: 'Answer policy - cite, preserve links, format',
    active: true,
    dry_runnable: true,
    activates_in: null,
    variables: [],
    production_version: 1,
    staging_version: null,
    latest_version: 1,
    updated_at: NOW,
    updated_by_name: 'System (seed)',
    provider: null,
    model: null,
    reasoning_effort: null,
    supports_reasoning_effort: false,
  },
  {
    name: 'planner',
    role: 'Decompose task, order tool steps',
    active: false,
    dry_runnable: false,
    activates_in: 'M2.5',
    variables: [],
    production_version: 1,
    staging_version: null,
    latest_version: 1,
    updated_at: NOW,
    updated_by_name: 'System (seed)',
    provider: null,
    model: null,
    reasoning_effort: null,
    supports_reasoning_effort: false,
  },
  {
    name: 'semantic_compressor',
    role: 'Raw tool JSON → token-tight sentences',
    active: false,
    dry_runnable: false,
    activates_in: 'M2.5',
    variables: [],
    production_version: 1,
    staging_version: null,
    latest_version: 1,
    updated_at: NOW,
    updated_by_name: 'System (seed)',
    provider: null,
    model: null,
    reasoning_effort: null,
    supports_reasoning_effort: false,
  },
  {
    name: 'validator',
    role: 'Confidence-gate answer before send',
    active: false,
    dry_runnable: false,
    activates_in: 'M3a',
    variables: [],
    production_version: 1,
    staging_version: null,
    latest_version: 1,
    updated_at: NOW,
    updated_by_name: 'System (seed)',
    provider: null,
    model: null,
    reasoning_effort: null,
    supports_reasoning_effort: false,
  },
  {
    name: 'clarifier',
    role: 'Ask-vs-guess when query underspecified',
    active: false,
    dry_runnable: false,
    activates_in: 'M3a',
    variables: [],
    production_version: 1,
    staging_version: null,
    latest_version: 1,
    updated_at: NOW,
    updated_by_name: 'System (seed)',
    provider: null,
    model: null,
    reasoning_effort: null,
    supports_reasoning_effort: false,
  },
  {
    name: 'judge',
    role: 'Offline/online quality eval (LLM-as-judge)',
    active: false,
    dry_runnable: false,
    activates_in: 'M3b',
    variables: [],
    production_version: 1,
    staging_version: null,
    latest_version: 1,
    updated_at: NOW,
    updated_by_name: 'System (seed)',
    provider: null,
    model: null,
    reasoning_effort: null,
    supports_reasoning_effort: false,
  },
];

const REFORMULATOR_V1 = `You are a query reformulator. Rewrite the latest user turn into a single,
self-contained natural-language question that preserves all important entities
(ids, codes, dates, names) from the prior conversation.
- Resolve pronouns/ellipsis using the history.
- Keep it concise (<= 2 sentences).
- Do not answer the question. Output plain text only, no quotes, no prefix.

{{current_date}}`;

const ROUTER_V1 = `You classify a single user message from an in-app assistant. The user is
viewing ONE specific record (a case/form) on screen.
Answer YES when the message asks about the specific record in front of them.
Answer NO for catalog/data lookups, definitions, or general how-to questions.
Respond with exactly one word: YES or NO.`;

const ROUTER_V2 = `${ROUTER_V1}
Tie-breaker: if it could plausibly be about the open record, answer YES.`;

export const MOCK_VERSIONS: Record<string, PromptVersionsResponse> = {
  reformulator: {
    name: 'reformulator',
    role: 'Rewrite turn → standalone query',
    active: true,
    activates_in: null,
    variables: ['current_date'],
    labels: { production: 1, staging: null },
    versions: [
      {
        id: 'reformulator-1',
        version: 1,
        commit_message: 'Seed from hardcoded fallback',
        created_by_name: 'System (seed)',
        created_at: NOW,
        labels: ['production'],
      },
    ],
  },
  router: {
    name: 'router',
    role: 'Intent / routing (is-record-Q? how-to? handoff?)',
    active: true,
    activates_in: null,
    variables: [],
    labels: { production: 2, staging: 2 },
    versions: [
      {
        id: 'router-2',
        version: 2,
        commit_message: 'Add tie-breaker line',
        created_by_name: 'Jayson',
        created_at: NOW,
        labels: ['production', 'staging'],
      },
      {
        id: 'router-1',
        version: 1,
        commit_message: 'Seed from hardcoded fallback',
        created_by_name: 'System (seed)',
        created_at: '2026-07-02T09:00:00',
        labels: [],
      },
    ],
  },
  agent_system: singleVersion('agent_system', 'Thinker / executor ReAct core'),
  synthesizer: singleVersion('synthesizer', 'Answer policy - cite, preserve links, format'),
  planner: singleVersion('planner', 'Decompose task, order tool steps', false, 'M2.5'),
  semantic_compressor: singleVersion('semantic_compressor', 'Raw tool JSON → token-tight sentences', false, 'M2.5'),
  validator: singleVersion('validator', 'Confidence-gate answer before send', false, 'M3a'),
  clarifier: singleVersion('clarifier', 'Ask-vs-guess when query underspecified', false, 'M3a'),
  judge: singleVersion('judge', 'Offline/online quality eval (LLM-as-judge)', false, 'M3b'),
};

const MOCK_TEMPLATES: Record<string, Record<number, string>> = {
  reformulator: { 1: REFORMULATOR_V1 },
  router: { 1: ROUTER_V1, 2: ROUTER_V2 },
};

function singleVersion(
  name: string,
  role: string,
  active = true,
  activates_in: string | null = null,
): PromptVersionsResponse {
  return {
    name,
    role,
    active,
    activates_in,
    variables: [],
    labels: { production: 1, staging: null },
    versions: [
      {
        id: `${name}-1`,
        version: 1,
        commit_message: 'Seed from hardcoded fallback',
        created_by_name: 'System (seed)',
        created_at: NOW,
        labels: ['production'],
      },
    ],
  };
}

export function mockTemplateFor(name: string, version: number): string {
  return MOCK_TEMPLATES[name]?.[version] ?? `# ${name} v${version}\n(mock template body - edit me)`;
}

const DECLARED: (t: string) => string[] = () => [];

function tokensIn(template: string): string[] {
  return Array.from(template.matchAll(/\{\{\s*([a-zA-Z0-9_]+)\s*\}\}/g)).map((m) => m[1]);
}

export function mockSaveVersion(name: string, payload: SaveVersionPayload): Promise<PromptVersionDetail> {
  const meta = MOCK_VERSIONS[name];
  const declared = meta?.variables ?? DECLARED(payload.template);
  const found = tokensIn(payload.template);
  const unknown = found.filter((t) => !declared.includes(t));
  if (!payload.commit_message.trim()) {
    return Promise.reject(mkValidationError('Commit message required', [], []));
  }
  if (unknown.length) {
    return Promise.reject(mkValidationError(`Unknown template token(s): ${unknown.join(', ')}`, unknown, []));
  }
  const missing = declared.filter((d) => !found.includes(d));
  const nextVersion = Math.max(0, ...(meta?.versions.map((v) => v.version) ?? [0])) + 1;
  const detail: PromptVersionDetail = {
    id: `${name}-${nextVersion}`,
    name,
    version: nextVersion,
    template: payload.template,
    variables: declared,
    commit_message: payload.commit_message,
    created_by_name: 'You',
    created_at: new Date().toISOString(),
    labels: [],
  };
  // mutate mock store so history reflects the save during the session
  if (meta) {
    meta.versions.unshift({
      id: detail.id,
      version: nextVersion,
      commit_message: payload.commit_message,
      created_by_name: 'You',
      created_at: detail.created_at,
      labels: [],
    });
    MOCK_TEMPLATES[name] = { ...(MOCK_TEMPLATES[name] || {}), [nextVersion]: payload.template };
    const summary = MOCK_PROMPT_KEYS.find((k) => k.name === name);
    if (summary) summary.latest_version = nextVersion;
  }
  // attach missing warning for the caller to optionally surface
  (detail as PromptVersionDetail & { missing_vars?: string[] }).missing_vars = missing;
  return Promise.resolve(detail);
}

export function mockSetLabel(name: string, payload: SetLabelPayload): Promise<SetLabelResponse> {
  const meta = MOCK_VERSIONS[name];
  if (!meta) return Promise.reject(new Error('unknown key'));
  const target = meta.versions.find((v) => v.id === payload.version_id);
  if (!target) return Promise.reject(new Error('unknown version'));
  meta.versions.forEach((v) => {
    v.labels = v.labels.filter((l) => l !== payload.label);
  });
  target.labels = Array.from(new Set([...target.labels, payload.label]));
  meta.labels[payload.label] = target.version;
  const summary = MOCK_PROMPT_KEYS.find((k) => k.name === name);
  if (summary) {
    if (payload.label === 'production') summary.production_version = target.version;
    if (payload.label === 'staging') summary.staging_version = target.version;
  }
  return Promise.resolve({ labels: meta.labels });
}

export function mockDryRun(name: string, payload: DryRunPayload): Promise<DryRunResponse> {
  const summary = MOCK_PROMPT_KEYS.find((k) => k.name === name);
  if (summary && !summary.active) {
    return Promise.reject(new Error('Dormant key is not testable - it has no runtime call site yet.'));
  }
  return Promise.resolve({
    output: `**Mock dry-run** for \`${name}\` (${payload.version_id}).\n\nYou said: "${payload.message}".\n\nThis is a stubbed response - the Phase-2 backend runs the real assistant turn with only this prompt overridden.`,
    token_usage: { prompt_tokens: 812, completion_tokens: 143, total_tokens: 955 },
    tool_calls: [{ name: 'user_guides_read', ok: true }],
    used_overrides: { [name]: payload.version_id },
  });
}

function mkValidationError(message: string, unknown_tokens: string[], missing_vars: string[]): Error {
  const e = new Error(message) as Error & { validation?: SaveVersionError };
  e.validation = { error: message, unknown_tokens, missing_vars };
  return e;
}
