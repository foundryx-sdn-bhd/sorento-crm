'use client';

import { useMemo, useState } from 'react';
import { Plus } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { SectionSkeleton } from '@/components/common/SectionSkeleton';
import { useHasPermission } from '@/hooks/usePermissions';
import { formatDateTimeInMalaysia } from '@/lib/helpers';
import { useUnpricedModelsQuery } from '../../hooks/useModelPrices';
import ModelPriceModal from './ModelPriceModal';

const EDIT_PERMISSION = 'system.ai_assistant_settings.edit';

export default function UnpricedModelsList() {
  const [modalOpen, setModalOpen] = useState(false);
  const [lockedModel, setLockedModel] = useState<string | null>(null);

  const canEdit = useHasPermission(EDIT_PERMISSION);
  const { data, isLoading, isError, error } = useUnpricedModelsQuery();
  const rows = useMemo(() => data ?? [], [data]);

  if (isError) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Models without a price</CardTitle>
        </CardHeader>
        <p className="px-5 pb-5 text-sm text-destructive">
          {(error as Error | null)?.message || 'Unpriced models could not be loaded.'}
        </p>
      </Card>
    );
  }

  return (
    <>
      <Card>
        <CardHeader>
          <CardTitle>Models without a price</CardTitle>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <SectionSkeleton rows={3} />
          ) : rows.length === 0 ? (
            <div className="py-6 text-center">
              <p className="text-sm font-medium">Every model in use has a price</p>
              <p className="text-xs text-muted-foreground">
                Models seen in the usage log without a price appear here.
              </p>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[420px] text-sm">
                <thead>
                  <tr className="border-b text-xs text-muted-foreground">
                    <th className="py-2 text-left font-medium">Model</th>
                    <th className="py-2 text-right font-medium">Calls</th>
                    <th className="py-2 text-left font-medium ps-6">Last seen</th>
                    {canEdit ? <th className="w-32 py-2">
                        <span className="sr-only">Actions</span>
                      </th> : null}
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.model} className="border-b last:border-0">
                      <td className="max-w-[240px] truncate py-2 font-medium" title={r.model}>
                        {r.model}
                      </td>
                      <td className="py-2 text-right tabular-nums">{r.calls}</td>
                      <td className="py-2 ps-6 whitespace-nowrap text-muted-foreground">
                        {formatDateTimeInMalaysia(r.last_seen_at)}
                      </td>
                      {canEdit ? (
                        <td className="py-2 text-right">
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() => {
                              setLockedModel(r.model);
                              setModalOpen(true);
                            }}
                          >
                            <Plus className="size-4" />
                            Add price
                          </Button>
                        </td>
                      ) : null}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      <ModelPriceModal
        open={modalOpen}
        onOpenChange={setModalOpen}
        price={null}
        lockedModel={lockedModel}
      />
    </>
  );
}
