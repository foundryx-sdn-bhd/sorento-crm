'use client';

import { useEffect, useState } from 'react';
import { toast } from '@/lib/toast';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { usePromptKeys, useSetAgentModel } from '../../hooks/useAIAssistantPrompts';
import { MODEL_OPTIONS, PROVIDER_OPTIONS } from '../../lib/modelOptions';
import { useProviderModels } from '@/hooks/useProviderModels';

const EFFORT_OPTIONS = [
  { value: 'none', label: 'None' },
  { value: 'low', label: 'Low' },
  { value: 'medium', label: 'Medium' },
  { value: 'high', label: 'High' },
];

/**
 * Which LLM this one agent runs on.
 *
 * Every agent shared one model until now, which meant the setting was tuned for the
 * hardest job or for none of them: reading a misspelt customer sentence and writing an
 * explanatory paragraph do not want the same model. Left blank, the agent keeps using
 * the global assistant model - so nothing changes until someone makes a choice here.
 */
export function AgentModelCard({ name, canEdit }: { name: string; canEdit: boolean }) {
  const keysQuery = usePromptKeys();
  const setModel = useSetAgentModel(name);
  const row = (keysQuery.data || []).find((r) => r.name === name);

  const [provider, setProvider] = useState('');
  const [model, setModel_] = useState('');
  const [effort, setEffort] = useState('');
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    if (row && !loaded) {
      setProvider(row.provider ?? '');
      setModel_(row.model ?? '');
      setEffort(row.reasoning_effort ?? '');
      setLoaded(true);
    }
  }, [row, loaded]);

  // Asked of the provider rather than read off a table here, so a model the
  // provider has retired stops being offered the moment it is retired. A model
  // typed by hand is still legitimate - the dropdown offers, it does not restrict -
  // and the built-in list stands in when the provider cannot be reached.
  const modelsQuery = useProviderModels(provider);
  const options = modelsQuery.data?.models ?? MODEL_OPTIONS[provider] ?? [];
  const dirty =
    (row?.provider ?? '') !== provider ||
    (row?.model ?? '') !== model ||
    (row?.reasoning_effort ?? '') !== effort;

  const save = async () => {
    try {
      await setModel.mutateAsync({
        provider: provider || null,
        model: model || null,
        reasoning_effort: effort || null,
        label: row?.runtime_label || 'production',
      });
      toast.success(model ? `${name} now runs on ${model}` : `${name} back on the default model`);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to set the model');
    }
  };

  return (
    <Card className="h-fit">
      <CardHeader>
        <CardTitle className="text-base">Runs on</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <div>
          <label className="mb-1 block text-xs uppercase tracking-wide text-muted-foreground">
            Provider
          </label>
          <SearchableSelect
            value={provider}
            onChange={(v) => {
              setProvider(v);
              setModel_('');
            }}
            options={PROVIDER_OPTIONS}
            placeholder="Default"
            disabled={!canEdit}
          />
        </div>
        <div>
          <label className="mb-1 block text-xs uppercase tracking-wide text-muted-foreground">
            Model
          </label>
          {provider ? (
            <SearchableSelect
              value={model}
              onChange={setModel_}
              options={options}
              placeholder="Select a model"
              disabled={!canEdit}
            />
          ) : null}
          <Input
            className="mt-2 font-mono text-xs"
            placeholder="or type a model name"
            value={model}
            onChange={(e) => setModel_(e.target.value)}
            disabled={!canEdit}
          />
        </div>
        {row?.supports_reasoning_effort ? (
          <div>
            <label className="mb-1 block text-xs uppercase tracking-wide text-muted-foreground">
              Reasoning effort
            </label>
            <SearchableSelect
              value={effort}
              onChange={setEffort}
              options={EFFORT_OPTIONS}
              placeholder="Default"
              clearable
              disabled={!canEdit}
            />
          </div>
        ) : null}
        <p className="text-xs text-muted-foreground">
          {row?.model
            ? 'Clear both fields to put this agent back on the global assistant model.'
            : 'Empty means this agent uses the global assistant model.'}
        </p>
        {canEdit ? (
          <Button
            size="sm"
            className="w-full"
            onClick={save}
            disabled={!dirty || setModel.isPending}
          >
            {setModel.isPending ? 'Saving…' : 'Save model'}
          </Button>
        ) : null}
      </CardContent>
    </Card>
  );
}
