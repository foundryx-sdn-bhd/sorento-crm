/**
 * Word maps for the audit actor fields (identity S0, AC-13). Plain module, no React.
 *
 * The backend resolves `actor_label` to words; these helpers only map the raw
 * `actor_type` / `auth_method` codes and pick the display fallback. Never an id.
 */

const ACTOR_KIND_LABELS: Record<string, string> = {
  user: 'Staff',
  contact: 'Portal contact',
  integration: 'Integration',
  worker: 'Background job',
  scheduler: 'Scheduled',
  public_link: 'Public link',
  system: 'System',
};

const AUTH_METHOD_LABELS: Record<string, string> = {
  password: 'Email and password',
  phone_otp: 'Phone code',
  portal_link: 'Portal link',
  portal_token: 'Portal token',
  api_key: 'API key',
  impersonation: 'Impersonation',
  dev_login: 'Dev sign-in',
};

/** Actor kind in words; null for legacy / unset rows (the caller hides the row). */
export function actorKindLabel(actorType: string | null | undefined): string | null {
  if (!actorType) return null;
  return ACTOR_KIND_LABELS[actorType] ?? null;
}

/** Sign-in method in words; "-" when unset. */
export function authMethodLabel(authMethod: string | null | undefined): string {
  if (!authMethod) return '-';
  return AUTH_METHOD_LABELS[authMethod] ?? '-';
}

interface AuditActorFields {
  actor_label?: string | null;
  user_display_name?: string | null;
}

/** Who did it, in words: actor_label, else user_display_name, else "System". Never user_id. */
export function actorDisplay(entry: AuditActorFields): string {
  return entry.actor_label || entry.user_display_name || 'System';
}
