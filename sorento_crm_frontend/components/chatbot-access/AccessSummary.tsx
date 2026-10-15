import type { ContactAccess, Registry } from '@/services/chatbotAccessService';

export interface AccessSummaryProps {
  mode: 'contact' | 'role';
  registry: Registry;
  effective: Pick<ContactAccess['effective'], 'domains' | 'attributes' | 'sees_all_customers'>;
  customersLabel: string;
  /** Field add-overrides per topic, beyond what the roles tick. */
  extras?: { domain: string; count: number }[];
}

/** The plain "can / cannot" box at the top of the contact and role pages. */
export default function AccessSummary({ mode, registry, effective, customersLabel, extras = [] }: AccessSummaryProps) {
  const can: string[] = [];
  const cannot: string[] = [];
  for (const d of registry.domains.filter((x) => x.supported !== false)) {
    (effective.domains.includes(d.name) ? can : cannot).push(d.access_label || d.label);
    for (const f of d.fields.filter((x) => x.kind === 'report')) {
      const granted = effective.domains.includes(d.name) && effective.attributes.includes(f.key);
      (granted ? can : cannot).push(f.label);
    }
  }
  return (
    <div className="rounded-lg border bg-muted/30 p-4 text-sm">
      <h3 className="mb-2 font-semibold">
        {mode === 'contact' ? 'This contact can' : 'Contacts with this role can'}
      </h3>
      <ul className="space-y-1">
        <li>ask about: {can.length ? can.join(', ') : 'nothing yet'}</li>
        {cannot.length > 0 && <li className="text-muted-foreground">cannot ask: {cannot.join(', ')}</li>}
        {extras.map((e) => (
          <li key={e.domain}>
            plus {e.count} extra {e.domain.replace(/_/g, ' ')} details
          </li>
        ))}
        <li>see {customersLabel}</li>
      </ul>
    </div>
  );
}
