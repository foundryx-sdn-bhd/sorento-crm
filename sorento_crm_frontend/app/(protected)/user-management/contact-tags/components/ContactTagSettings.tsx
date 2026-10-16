'use client';

import { useMemo, useState, type ReactNode } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Card,
  CardContent,
  CardHeader,
  CardHeading,
  CardTitle,
} from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Switch } from '@/components/ui/switch';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';
import { SearchableMultiSelect } from '@/components/common/SearchableMultiSelect';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { toast } from '@/lib/toast';
import { updateContactTag } from '../services/contactTagService';
import {
  EMPTY_CATALOG,
  getSettingsCatalog,
} from '../services/contactTagSettingsCatalogService';
import type {
  CatalogOption,
  ContactTag,
  SettingsCatalog,
  TagSettingsValue,
} from '../types/contactTag.types';

/**
 * The tag's Access settings: 15 groups, view and edit in the same layout. A group is "set" when
 * its section is present in the settings object; a bool key is Not set (absent), On or Off.
 *
 * Built from the shared primitives (ToggleGroup, SearchableMultiSelect, SearchableSelect, Switch)
 * rather than the contact page's section components: those are each wired to their own fetch and
 * save for ONE contact, and none takes a value and an onChange.
 */

type S = TagSettingsValue;
type Sections = readonly string[];

const GROUPS: { id: string; title: string; sections: Sections }[] = [
  { id: 'access_types', title: 'Access types', sections: ['access_types'] },
  { id: 'brands', title: 'Brands', sections: ['brands'] },
  { id: 'companies', title: 'Companies', sections: ['companies'] },
  {
    id: 'market_segments',
    title: 'Market segments',
    sections: ['market_segments'],
  },
  { id: 'regions', title: 'Regions', sections: ['regions'] },
  { id: 'chatbot', title: 'Chatbot', sections: ['chatbot'] },
  {
    id: 'portal_forms',
    title: 'Portal forms',
    sections: ['portal_forms', 'requires_registered_project'],
  },
  { id: 'field_reveals', title: 'Field reveals', sections: ['field_reveals'] },
  { id: 'agents', title: 'Chatbot agents', sections: ['agents'] },
  {
    id: 'agent_fields',
    title: 'Agent field access',
    sections: ['agent_fields'],
  },
  { id: 'media', title: 'Media access', sections: ['media'] },
  {
    id: 'attachment_types',
    title: 'Attachment types',
    sections: ['attachment_types'],
  },
  {
    id: 'stock_visibility',
    title: 'Stock visibility',
    sections: ['stock_visibility'],
  },
  {
    id: 'spec_visibility',
    title: 'Spec visibility',
    sections: ['spec_visibility'],
  },
  {
    id: 'outbound_enabled',
    title: 'Messaging (outbound)',
    sections: ['outbound_enabled'],
  },
];

const EMPTY_VALUE: Record<string, unknown> = {
  access_types: [],
  brands: 'all',
  companies: [],
  market_segments: [],
  regions: ['west'],
  chatbot: {},
  portal_forms: {},
  field_reveals: {},
  agents: {},
  agent_fields: [],
  media: {},
  attachment_types: [],
  stock_visibility: {},
  spec_visibility: {},
  outbound_enabled: true,
};

const CHATBOT_BOOLS: [string, string][] = [
  ['chatbot_allowed', 'AI replies'],
  ['chatbot_stock_allowed', 'Stock checks'],
  ['escalation_allowed', 'Escalation'],
  ['packing_list_allowed', 'Packing list'],
  ['notify_salesman', 'Notify salesman'],
  ['chatbot_eta_offset_applied', 'ETA buffer days'],
  ['chatbot_skip_period_questions', 'Skip date-range questions'],
  ['chatbot_files_as_links', 'Files as links'],
];
const MEMORY: CatalogOption[] = [
  { value: 'off', label: 'Off' },
  { value: 'conversation', label: 'Conversation' },
  { value: 'episodes', label: 'Past conversations' },
  { value: 'full', label: 'Full' },
];
const TIERS: CatalogOption[] = [
  { value: 'dealer', label: 'Dealer' },
  { value: 'office', label: 'Office' },
  { value: 'end_user', label: 'End user' },
];
const REGIONS: CatalogOption[] = [
  { value: 'west', label: 'West Malaysia' },
  { value: 'east', label: 'East Malaysia' },
];
const MODES: CatalogOption[] = [
  { value: 'detailed', label: 'Detailed' },
  { value: 'compact', label: 'Compact' },
  { value: 'availability', label: 'Availability' },
];
// The kinds a tag may switch; static so the rows show before any catalog arrives.
const PORTAL_FORMS: CatalogOption[] = [
  { value: 'complaint', label: 'Complaint' },
  { value: 'stock_inquiry', label: 'Stock Inquiry' },
  { value: 'purchase_request', label: 'Purchase Request' },
  { value: 'sponsorship_form', label: 'Sponsorship Form' },
  { value: 'price_tag_request', label: 'Price Tag Request' },
  { value: 'sales_opportunity', label: 'Sales Opportunities' },
  { value: 'customer_asks', label: 'Customer asks' },
  { value: 'conversation', label: 'Conversation' },
];

const labelOf = (options: CatalogOption[], value: string) =>
  options.find((o) => o.value === value)?.label ?? 'Removed';

function isEmptyObject(v: unknown) {
  return (
    typeof v === 'object' &&
    v !== null &&
    !Array.isArray(v) &&
    Object.keys(v).length === 0
  );
}

// ------------------------------------------------------------------ rows

/** Not set / On / Off (agents: Not set / Allow / Deny); view shows the set value only. */
function BoolRow({
  id,
  label,
  value,
  onChange,
  editing,
  words = ['On', 'Off'],
}: {
  id: string;
  label: string;
  value: boolean | undefined;
  onChange: (v: boolean | undefined) => void;
  editing: boolean;
  words?: [string, string];
}) {
  if (!editing && value === undefined) return null;
  const current = value === undefined ? 'unset' : value ? 'on' : 'off';
  return (
    <div
      data-testid={`setting-row-${id}`}
      className="flex flex-wrap items-center justify-between gap-2 py-1"
    >
      <span className="min-w-0 truncate text-sm" title={label}>
        {label}
      </span>
      {editing ? (
        <ToggleGroup
          type="single"
          variant="outline"
          size="sm"
          value={current}
          onValueChange={(v) =>
            v && onChange(v === 'unset' ? undefined : v === 'on')
          }
        >
          <ToggleGroupItem value="unset">Not set</ToggleGroupItem>
          <ToggleGroupItem value="on">{words[0]}</ToggleGroupItem>
          <ToggleGroupItem value="off">{words[1]}</ToggleGroupItem>
        </ToggleGroup>
      ) : (
        <Badge variant={value ? 'success' : 'secondary'} appearance="light">
          {value ? words[0] : words[1]}
        </Badge>
      )}
    </div>
  );
}

function Labelled({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-1 py-1">
      <span className="text-sm">{label}</span>
      {children}
    </div>
  );
}

function Pills({ labels }: { labels: string[] }) {
  return labels.length ? (
    <div className="flex flex-wrap gap-1">
      {labels.map((l) => (
        <Badge
          key={l}
          variant="secondary"
          appearance="light"
          className="font-normal"
        >
          {l}
        </Badge>
      ))}
    </div>
  ) : (
    <span className="text-sm text-muted-foreground">None</span>
  );
}

function PickList({
  value,
  onChange,
  options,
  editing,
  placeholder,
}: {
  value: string[] | undefined;
  onChange: (v: string[] | undefined) => void;
  options: CatalogOption[];
  editing: boolean;
  placeholder: string;
}) {
  if (!editing)
    return value === undefined ? null : (
      <Pills labels={value.map((v) => labelOf(options, v))} />
    );
  return (
    <SearchableMultiSelect
      value={value ?? []}
      onChange={(v) => onChange(v.length ? v : undefined)}
      options={options}
      placeholder={placeholder}
      emptyMessage="No options."
    />
  );
}

function PickOne({
  value,
  onChange,
  options,
  editing,
  placeholder,
}: {
  value: string | undefined;
  onChange: (v: string | undefined) => void;
  options: CatalogOption[];
  editing: boolean;
  placeholder: string;
}) {
  if (!editing)
    return value === undefined ? null : (
      <span className="text-sm">{labelOf(options, value)}</span>
    );
  return (
    <SearchableSelect
      value={value ?? ''}
      onChange={(v) => onChange(v || undefined)}
      options={options}
      placeholder={placeholder}
      clearable
    />
  );
}

/** Replace one key of an object value, dropping it when undefined. */
function put(
  obj: Record<string, unknown> | undefined,
  key: string,
  v: unknown,
) {
  const next = { ...(obj ?? {}) };
  if (v === undefined) delete next[key];
  else next[key] = v;
  return next;
}

// ------------------------------------------------------------------ group bodies

interface BodyProps {
  s: S;
  set: (section: string, v: unknown) => void;
  editing: boolean;
  catalog: SettingsCatalog;
}

function GroupBody({
  id,
  s,
  set,
  editing,
  catalog,
}: BodyProps & { id: string }) {
  switch (id) {
    case 'access_types':
    case 'companies':
    case 'market_segments':
    case 'attachment_types': {
      const options = catalog[id as 'access_types'];
      return (
        <PickList
          value={s[id] as string[] | undefined}
          onChange={(v) => set(id, v ?? [])}
          options={options}
          editing={editing}
          placeholder={`Select ${id.replace('_', ' ')}`}
        />
      );
    }
    case 'brands': {
      const all = s.brands === 'all';
      return (
        <div className="space-y-2">
          {editing ? (
            <div className="flex items-center gap-2 text-sm">
              <Switch
                aria-label="All brands"
                checked={all}
                onCheckedChange={(v) => set('brands', v ? 'all' : [])}
              />
              All brands
            </div>
          ) : all ? (
            <Pills labels={['All brands']} />
          ) : null}
          {!all ? (
            <PickList
              value={s.brands as string[] | undefined}
              onChange={(v) => set('brands', v ?? [])}
              options={catalog.brands}
              editing={editing}
              placeholder="Select brands"
            />
          ) : null}
        </div>
      );
    }
    case 'regions':
      return (
        <PickList
          value={s.regions as string[] | undefined}
          onChange={(v) => set('regions', v ?? [])}
          options={REGIONS}
          editing={editing}
          placeholder="Select regions"
        />
      );
    case 'chatbot': {
      const c = (s.chatbot ?? {}) as Record<string, unknown>;
      const upd = (k: string, v: unknown) => set('chatbot', put(c, k, v));
      return (
        <div>
          {CHATBOT_BOOLS.map(([k, label]) => (
            <BoolRow
              key={k}
              id={k}
              label={label}
              value={c[k] as boolean | undefined}
              onChange={(v) => upd(k, v)}
              editing={editing}
            />
          ))}
          {editing || c.memory_level !== undefined ? (
            <Labelled label="Memory level">
              <PickOne
                value={c.memory_level as string | undefined}
                onChange={(v) => upd('memory_level', v)}
                options={MEMORY}
                editing={editing}
                placeholder="Not set"
              />
            </Labelled>
          ) : null}
          {editing || c.tier !== undefined ? (
            <Labelled label="Tier">
              <PickOne
                value={c.tier as string | undefined}
                onChange={(v) => upd('tier', v)}
                options={TIERS}
                editing={editing}
                placeholder="Not set"
              />
            </Labelled>
          ) : null}
        </div>
      );
    }
    case 'portal_forms': {
      const p = (s.portal_forms ?? {}) as Record<string, boolean>;
      return (
        <div>
          {PORTAL_FORMS.map((o) => (
            <BoolRow
              key={o.value}
              id={o.value}
              label={o.label}
              value={p[o.value]}
              onChange={(v) => set('portal_forms', put(p, o.value, v))}
              editing={editing}
            />
          ))}
          <BoolRow
            id="requires_registered_project"
            label="Requires registered project"
            value={s.requires_registered_project as boolean | undefined}
            onChange={(v) => set('requires_registered_project', v)}
            editing={editing}
          />
        </div>
      );
    }
    case 'field_reveals':
    case 'agents': {
      const m = (s[id] ?? {}) as Record<string, boolean>;
      const options = id === 'agents' ? catalog.agents : catalog.field_reveals;
      const keys = editing ? options.map((o) => o.value) : Object.keys(m);
      return (
        <div>
          {keys.map((k) => (
            <BoolRow
              key={k}
              id={k}
              label={labelOf(options, k)}
              value={m[k]}
              onChange={(v) => set(id, put(m, k, v))}
              editing={editing}
              words={id === 'agents' ? ['Allow', 'Deny'] : ['On', 'Off']}
            />
          ))}
        </div>
      );
    }
    case 'agent_fields':
      return (
        <AgentFields s={s} set={set} editing={editing} catalog={catalog} />
      );
    case 'media':
      return <MediaBody s={s} set={set} editing={editing} catalog={catalog} />;
    case 'stock_visibility':
    case 'spec_visibility': {
      const v = (s[id] ?? {}) as Record<string, unknown>;
      const upd = (k: string, x: unknown) => set(id, put(v, k, x));
      const stock = id === 'stock_visibility';
      const incl = stock ? 'warehouse_ids' : 'spec_keys';
      const excl = stock ? 'excluded_warehouse_ids' : 'excluded_spec_keys';
      const options = stock ? catalog.warehouses : catalog.spec_keys;
      return (
        <div>
          {stock && (editing || v.mode !== undefined) ? (
            <Labelled label="Mode">
              <PickOne
                value={v.mode as string | undefined}
                onChange={(x) => upd('mode', x)}
                options={MODES}
                editing={editing}
                placeholder="Not set"
              />
            </Labelled>
          ) : null}
          {editing || v[incl] !== undefined ? (
            <Labelled
              label={stock ? 'Only these locations' : 'Only these specs'}
            >
              <PickList
                value={v[incl] as string[] | undefined}
                onChange={(x) => upd(incl, x)}
                options={options}
                editing={editing}
                placeholder="All"
              />
            </Labelled>
          ) : null}
          {editing || v[excl] !== undefined ? (
            <Labelled
              label={stock ? 'Except these locations' : 'Hide these specs'}
            >
              <PickList
                value={v[excl] as string[] | undefined}
                onChange={(x) => upd(excl, x)}
                options={options}
                editing={editing}
                placeholder="None"
              />
            </Labelled>
          ) : null}
          {stock ? (
            <BoolRow
              id="hide_zero_locations"
              label="Hide empty locations"
              value={v.hide_zero_locations as boolean | undefined}
              onChange={(x) => upd('hide_zero_locations', x)}
              editing={editing}
            />
          ) : null}
        </div>
      );
    }
    default:
      return (
        <BoolRow
          id="outbound_enabled"
          label="Messaging"
          value={s.outbound_enabled as boolean | undefined}
          onChange={(v) => set('outbound_enabled', v)}
          editing={editing}
        />
      );
  }
}

type Entry = {
  agent_code: string;
  resource: string;
  field_key: string;
  is_allowed: boolean;
};

function AgentFields({ s, set, editing, catalog }: BodyProps) {
  const entries = (s.agent_fields ?? []) as Entry[];
  const [draft, setDraft] = useState({
    agent_code: '',
    resource: '',
    field_key: '',
  });
  const agentName = (code: string) =>
    catalog.agents.find((a) => a.code === code)?.label ?? 'Removed agent';
  return (
    <div>
      {entries.map((e, i) => (
        <BoolRow
          key={`${e.agent_code}.${e.resource}.${e.field_key}`}
          id={`${e.agent_code}.${e.resource}.${e.field_key}`}
          label={`${agentName(e.agent_code)}: ${e.field_key}`}
          value={e.is_allowed}
          onChange={(v) =>
            set(
              'agent_fields',
              v === undefined
                ? entries.filter((_, j) => j !== i)
                : entries.map((x, j) =>
                    j === i ? { ...x, is_allowed: v } : x,
                  ),
            )
          }
          editing={editing}
          words={['Allow', 'Deny']}
        />
      ))}
      {editing ? (
        <div className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-4">
          <SearchableSelect
            value={draft.agent_code}
            onChange={(v) => setDraft((d) => ({ ...d, agent_code: v }))}
            options={catalog.agents.map((a) => ({
              value: a.code ?? '',
              label: a.label,
            }))}
            placeholder="Agent"
            clearable
          />
          <Input
            placeholder="Resource"
            value={draft.resource}
            onChange={(e) =>
              setDraft((d) => ({ ...d, resource: e.target.value }))
            }
          />
          <Input
            placeholder="Field"
            value={draft.field_key}
            onChange={(e) =>
              setDraft((d) => ({ ...d, field_key: e.target.value }))
            }
          />
          <Button
            type="button"
            variant="outline"
            disabled={
              !draft.agent_code ||
              !draft.resource.trim() ||
              !draft.field_key.trim()
            }
            onClick={() => {
              set('agent_fields', [
                ...entries,
                {
                  ...draft,
                  resource: draft.resource.trim(),
                  field_key: draft.field_key.trim(),
                  is_allowed: true,
                },
              ]);
              setDraft({ agent_code: '', resource: '', field_key: '' });
            }}
          >
            Add field
          </Button>
        </div>
      ) : null}
    </div>
  );
}

function MediaBody({ s, set, editing }: BodyProps) {
  const media = (s.media ?? {}) as Record<string, Record<string, unknown>>;
  const updMod = (m: string, k: string, v: unknown) => {
    const body = put(media[m], k, v);
    set('media', put(media, m, Object.keys(body).length ? body : undefined));
  };
  const num = (m: string, k: string, label: string) => {
    const v = media[m]?.[k] as number | undefined;
    if (!editing && v === undefined) return null;
    return (
      <Labelled label={label}>
        {editing ? (
          <Input
            type="number"
            min={0}
            value={v ?? ''}
            onChange={(e) =>
              updMod(
                m,
                k,
                e.target.value === '' ? undefined : Number(e.target.value),
              )
            }
          />
        ) : (
          <span className="text-sm">{v}</span>
        )}
      </Labelled>
    );
  };
  const langs = (m: string) => {
    const v = media[m]?.languages as string[] | undefined;
    if (!editing && v === undefined) return null;
    return (
      <Labelled label="Languages">
        {editing ? (
          <Input
            placeholder="en, ms, zh"
            value={(v ?? []).join(', ')}
            onChange={(e) => {
              const list = e.target.value
                .split(',')
                .map((x) => x.trim())
                .filter(Boolean);
              updMod(m, 'languages', e.target.value.trim() ? list : undefined);
            }}
          />
        ) : (
          <Pills labels={v ?? []} />
        )}
      </Labelled>
    );
  };
  return (
    <div className="space-y-3">
      {(['image', 'voice'] as const).map((m) => (
        <div key={m}>
          <BoolRow
            id={`${m}.is_allowed`}
            label={`${m === 'image' ? 'Image' : 'Voice'} access`}
            value={media[m]?.is_allowed as boolean | undefined}
            onChange={(v) => updMod(m, 'is_allowed', v)}
            editing={editing}
          />
          {num(
            m,
            'monthly_limit',
            `${m === 'image' ? 'Image' : 'Voice'} monthly limit`,
          )}
          {m === 'voice'
            ? num(m, 'max_clip_seconds', 'Voice clip length (seconds)')
            : null}
          {langs(m)}
        </div>
      ))}
    </div>
  );
}

// ------------------------------------------------------------------ section

/** Drop a group's empty maps; arrays stay (an empty list is a real "none"). */
function cleaned(settings: S): S {
  const out: S = {};
  for (const [k, v] of Object.entries(settings)) {
    if (v === undefined || (isEmptyObject(v) && k !== 'media')) continue;
    if (k === 'media' && isEmptyObject(v)) continue;
    out[k] = v;
  }
  return out;
}

export default function ContactTagSettings({
  tag,
  editing,
  onDone,
}: {
  tag: ContactTag;
  editing: boolean;
  onDone: () => void;
}) {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<S>(() => ({ ...(tag.settings ?? {}) }));
  const [error, setError] = useState<string | null>(null);
  const { data } = useQuery({
    queryKey: ['contact-tag-settings-catalog'],
    queryFn: getSettingsCatalog,
    staleTime: 60_000,
  });
  const catalog = useMemo(() => data ?? EMPTY_CATALOG, [data]);
  const s: S = editing ? draft : (tag.settings ?? {});

  const save = useMutation({
    mutationFn: () => updateContactTag(tag.id, { settings: cleaned(draft) }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['contact-tag'] });
      queryClient.invalidateQueries({ queryKey: ['contact-tags'] });
      queryClient.invalidateQueries({ queryKey: ['respond-contacts'] });
      toast.success('Tag settings saved');
      onDone();
    },
    onError: (e: Error) => setError(e.message),
  });

  const setSection = (section: string, v: unknown) =>
    setDraft((d) => {
      const next = { ...d };
      if (v === undefined) delete next[section];
      else next[section] = v;
      return next;
    });

  const isSet = (sections: Sections) =>
    sections.some((x) => s[x] !== undefined);
  const toggle = (sections: Sections, on: boolean) =>
    setDraft((d) => {
      const next = { ...d };
      sections.forEach((x, i) => {
        if (!on) delete next[x];
        else if (i === 0) next[x] = EMPTY_VALUE[x];
      });
      return next;
    });

  return (
    <Card>
      <CardHeader>
        <CardHeading>
          <CardTitle>Access settings</CardTitle>
        </CardHeading>
      </CardHeader>
      <CardContent className="space-y-3">
        {GROUPS.map((g) => {
          const on = isSet(g.sections);
          return (
            <div
              key={g.id}
              data-testid="settings-group"
              className="rounded-md border p-3"
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h4
                  data-testid="settings-group-title"
                  className="text-sm font-medium"
                >
                  {g.title}
                </h4>
                {editing ? (
                  <label className="flex items-center gap-2 text-sm">
                    <Switch
                      aria-label={`Set on this tag: ${g.title}`}
                      checked={on}
                      onCheckedChange={(v) => toggle(g.sections, v)}
                    />
                    Set on this tag
                  </label>
                ) : null}
              </div>
              {on ? (
                <div className="mt-2">
                  <GroupBody
                    id={g.id}
                    s={s}
                    set={setSection}
                    editing={editing}
                    catalog={catalog}
                  />
                </div>
              ) : !editing ? (
                <p className="mt-1 text-sm text-muted-foreground">Not set</p>
              ) : null}
            </div>
          );
        })}
        {editing ? (
          <div className="flex flex-wrap items-center justify-end gap-2 pt-2">
            {error ? (
              <p className="mr-auto text-sm text-destructive" role="alert">
                {error}
              </p>
            ) : null}
            <Button
              variant="outline"
              onClick={onDone}
              disabled={save.isPending}
            >
              Cancel
            </Button>
            <Button
              onClick={() => {
                setError(null);
                save.mutate();
              }}
              disabled={save.isPending}
            >
              Save
            </Button>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}
