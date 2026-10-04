'use client';

import { Check, ExternalLink, Plus } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { SectionSkeleton } from '@/components/common/SectionSkeleton';
import { formatDateTimeInMalaysia } from '@/lib/helpers';
import { tokensInTemplate } from '../../lib/promptVars';
import type { RegistryVariableRow } from '../../services/aiPromptsService';

/** Sources that already reach the model on every turn outside the system prompt. */
const SENT_EVERY_TURN = new Set(['brands']);

/**
 * "Wired to this agent" (PLAN-prompt-dynamic-30sep R5a): every registry the prompt can
 * render, with its row count, last change, whether the current draft uses it, and a link
 * to the page that edits it. Insert puts the variable back into the wording.
 */
export function WiredPanel({
  variables,
  draft,
  onInsert,
  isLoading,
  canEdit = true,
}: {
  variables: RegistryVariableRow[];
  draft: string;
  onInsert: (name: string) => void;
  isLoading: boolean;
  canEdit?: boolean;
}) {
  const used = new Set(tokensInTemplate(draft));
  const inWording = variables.filter((v) => used.has(v.name)).length;

  return (
    <Card data-testid="wired-panel">
      <CardHeader className="flex flex-row items-center justify-between gap-2">
        <CardTitle className="text-base">Wired to this agent</CardTitle>
        {variables.length > 0 ? (
          <span className="text-xs text-muted-foreground">
            {inWording} of {variables.length} in wording
          </span>
        ) : null}
      </CardHeader>
      <CardContent className="space-y-0 p-0">
        {isLoading ? (
          <div className="p-4">
            <SectionSkeleton rows={4} />
          </div>
        ) : variables.length === 0 ? (
          <div className="p-4" data-testid="wired-empty">
            <p className="text-sm font-medium">Nothing wired</p>
            <p className="text-xs text-muted-foreground">This prompt reads no registry.</p>
          </div>
        ) : (
          variables.map((v) => {
            const isUsed = used.has(v.name);
            return (
              <div
                key={v.name}
                data-testid={`wired-row-${v.name}`}
                className="space-y-1 border-t px-4 py-3 first:border-t-0"
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate text-sm font-medium" title={v.source}>
                    {v.label}
                  </span>
                  <span className="flex shrink-0 items-center gap-2 text-xs tabular-nums text-muted-foreground">
                    {v.count}
                    {v.href ? (
                      // A new tab: an in-app navigation would drop the unsaved draft with no
                      // warning (the page only guards a full unload).
                      <a
                        href={v.href}
                        target="_blank"
                        rel="noopener"
                        className="text-muted-foreground hover:text-foreground"
                        aria-label={`Open ${v.source}`}
                        title={v.source}
                      >
                        <ExternalLink className="size-3.5" />
                      </a>
                    ) : null}
                  </span>
                </div>
                <div className="flex items-center justify-between gap-2 text-xs text-muted-foreground">
                  <span className="truncate">
                    {v.last_changed ? `Changed ${formatDateTimeInMalaysia(v.last_changed)}` : v.source}
                  </span>
                  {isUsed ? (
                    <span className="flex shrink-0 items-center gap-1 text-emerald-600">
                      <Check className="size-3.5" /> In wording
                    </span>
                  ) : (
                    <span className="flex shrink-0 items-center gap-2">
                      not in wording
                      {canEdit ? (
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          className="h-6 px-2 text-xs"
                          onClick={() => onInsert(v.name)}
                          data-testid={`wired-insert-${v.name}`}
                        >
                          <Plus className="size-3" /> Insert
                        </Button>
                      ) : null}
                    </span>
                  )}
                </div>
                {SENT_EVERY_TURN.has(v.name) ? (
                  <Badge variant="warning" appearance="light" size="sm">
                    Also sent every turn
                  </Badge>
                ) : null}
              </div>
            );
          })
        )}
      </CardContent>
    </Card>
  );
}
