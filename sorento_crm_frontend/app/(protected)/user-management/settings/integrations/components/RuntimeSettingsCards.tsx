'use client';

import { useState } from 'react';
import { CopyIcon, LoaderCircleIcon } from 'lucide-react';
import { toast } from '@/lib/toast';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Skeleton } from '@/components/ui/skeleton';
import { Switch } from '@/components/ui/switch';

import { useRuntimeSettings, useSaveRuntimeSettings } from '../hooks/useRuntimeSettings';
import type {
  RuntimeSettingField,
  RuntimeSettingName,
  RuntimeSettings,
  RuntimeSettingsUpdate,
} from '../services/runtimeSettingsService';

interface FieldSpec {
  name: RuntimeSettingName;
  label: string;
  placeholder?: string;
}

interface CardSpec {
  key: string;
  title: string;
  fields: FieldSpec[];
}

const CARDS: CardSpec[] = [
  {
    key: 'chat-analytics',
    title: 'Chat analytics',
    fields: [{ name: 'chat_topic_labeller_enabled', label: 'Nightly topic labeller' }],
  },
  {
    key: 'claude-connector',
    title: 'Claude connector',
    fields: [
      { name: 'connector_ss_to_product_secret', label: 'Shared service to CRM secret' },
      { name: 'connector_product_to_ss_secret', label: 'CRM to shared service secret' },
      { name: 'connector_connection_id', label: 'Connection ID' },
      {
        name: 'connector_ss_callback_url',
        label: 'Shared service callback URL',
        placeholder: 'https://…',
      },
      {
        name: 'analytics_ro_database_url',
        label: 'Read-only analytics database URL (optional)',
        placeholder: 'postgresql://…',
      },
    ],
  },
  {
    key: 'respond-webhook',
    title: 'Respond.io webhook',
    fields: [{ name: 'respond_webhook_secret', label: 'Webhook secret' }],
  },
];

const SOURCE_LABEL: Record<RuntimeSettingField['source'], string> = {
  db: 'Saved in CRM',
  env: 'Server env',
  unset: 'Not set',
};

const SOURCE_VARIANT: Record<RuntimeSettingField['source'], 'success' | 'info' | 'secondary'> = {
  db: 'success',
  env: 'info',
  unset: 'secondary',
};

function SourceBadge({ field }: { field: RuntimeSettingField }) {
  if (field.unreadable) {
    return (
      <Badge variant="destructive" appearance="light" size="sm">
        Saved value unreadable, enter it again
      </Badge>
    );
  }
  return (
    <Badge variant={SOURCE_VARIANT[field.source]} appearance="light" size="sm">
      {SOURCE_LABEL[field.source]}
    </Badge>
  );
}

function RespondWebhookFacts({ data }: { data: RuntimeSettings }) {
  const url =
    typeof window !== 'undefined'
      ? `${window.location.origin}${data.respond_webhook_path}`
      : data.respond_webhook_path;
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(url);
      toast.success('Webhook URL copied');
    } catch {
      toast.error('Could not copy the URL');
    }
  };
  return (
    <div className="space-y-3 text-sm">
      <div className="space-y-1.5">
        <Label>Webhook URL</Label>
        <div className="flex items-center gap-2">
          <Input value={url} readOnly className="font-mono text-xs" aria-label="Webhook URL" />
          <Button
            type="button"
            variant="outline"
            size="icon"
            mode="icon"
            onClick={copy}
            aria-label="Copy webhook URL"
            title="Copy webhook URL"
          >
            <CopyIcon className="size-4" />
          </Button>
        </div>
      </div>
      <div className="space-y-1">
        <Label>Signature header</Label>
        <p className="text-muted-foreground">
          <span className="font-mono">{data.respond_signature_headers.join(' or ')}</span>
          {' (HMAC-SHA256 of the raw body), or custom header '}
          <span className="font-mono">{data.respond_shared_secret_header}</span>
        </p>
      </div>
    </div>
  );
}

function RuntimeSettingsCard({ spec, data }: { spec: CardSpec; data: RuntimeSettings }) {
  const save = useSaveRuntimeSettings();
  const [draft, setDraft] = useState<RuntimeSettingsUpdate>({});
  const dirty = Object.keys(draft).length > 0;

  const submit = () =>
    save.mutate(draft, {
      onSuccess: () => setDraft({}),
    });
  const clear = (name: RuntimeSettingName) => save.mutate({ [name]: null });

  return (
    <Card>
      <CardHeader className="border-b border-border">
        <CardTitle>{spec.title}</CardTitle>
      </CardHeader>
      <CardContent className="py-8">
        <form
          onSubmit={(e) => {
            e.preventDefault();
            submit();
          }}
          className="space-y-6 lg:max-w-[640px] mx-auto"
        >
          {spec.key === 'respond-webhook' && <RespondWebhookFacts data={data} />}
          {spec.fields.map(({ name, label, placeholder }) => {
            const field = data.fields[name];
            if (!field) return null;
            const id = `runtime-${name}`;
            const clearButton = field.source === 'db' && (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => clear(name)}
                disabled={save.isPending}
              >
                {field.secret || typeof field.value !== 'boolean'
                  ? 'Clear saved value'
                  : 'Use server default'}
              </Button>
            );

            if (name === 'chat_topic_labeller_enabled') {
              const checked =
                typeof draft[name] === 'boolean' ? (draft[name] as boolean) : field.value === true;
              return (
                <div key={name} className="flex flex-wrap items-center justify-between gap-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <Label htmlFor={id}>{label}</Label>
                    <SourceBadge field={field} />
                  </div>
                  <div className="flex items-center gap-2">
                    {clearButton}
                    <Switch
                      id={id}
                      checked={checked}
                      onCheckedChange={(v) => setDraft((d) => ({ ...d, [name]: v }))}
                      aria-label={label}
                    />
                  </div>
                </div>
              );
            }

            const draftValue = typeof draft[name] === 'string' ? (draft[name] as string) : undefined;
            const shownValue = field.secret
              ? (draftValue ?? '')
              : (draftValue ?? (field.source === 'db' ? String(field.value ?? '') : ''));
            const fieldPlaceholder = field.secret
              ? field.masked
                ? `${field.masked} (leave blank to keep)`
                : 'Not set'
              : field.source === 'env'
                ? String(field.value ?? '')
                : placeholder;
            return (
              <div key={name} className="space-y-1.5">
                <div className="flex flex-wrap items-center gap-2">
                  <Label htmlFor={id}>{label}</Label>
                  <SourceBadge field={field} />
                </div>
                <div className="flex items-center gap-2">
                  <Input
                    id={id}
                    type={field.secret ? 'password' : 'text'}
                    autoComplete={field.secret ? 'new-password' : 'off'}
                    value={shownValue}
                    placeholder={fieldPlaceholder}
                    title={fieldPlaceholder}
                    onChange={(e) => setDraft((d) => ({ ...d, [name]: e.target.value }))}
                  />
                  {clearButton}
                </div>
              </div>
            );
          })}
          <Button type="submit" disabled={!dirty || save.isPending}>
            {save.isPending ? (
              <>
                <LoaderCircleIcon className="size-4 animate-spin mr-2" />
                Saving…
              </>
            ) : (
              'Save'
            )}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}

export default function RuntimeSettingsCards() {
  const query = useRuntimeSettings();

  if (query.isLoading) {
    return <Skeleton className="h-48 w-full" aria-label="Loading integration settings" />;
  }
  if (query.isError || !query.data) {
    return (
      <Card>
        <CardContent className="py-8 flex flex-wrap items-center justify-between gap-3">
          <p className="text-sm text-muted-foreground">
            {query.error instanceof Error
              ? query.error.message
              : 'Integration settings could not be loaded.'}
          </p>
          <Button variant="outline" onClick={() => query.refetch()} disabled={query.isFetching}>
            Retry
          </Button>
        </CardContent>
      </Card>
    );
  }
  return (
    <>
      {CARDS.map((spec) => (
        <RuntimeSettingsCard key={spec.key} spec={spec} data={query.data} />
      ))}
    </>
  );
}
