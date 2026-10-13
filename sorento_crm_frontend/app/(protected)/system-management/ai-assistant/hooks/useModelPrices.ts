'use client';

import { useCallback } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from '@/lib/toast';
import { useDeferredRowAction } from '@/hooks/useDeferredRowAction';
import {
  createModelPrice,
  listModelPrices,
  listUnpricedModels,
  updateModelPrice,
  type ModelPriceInput,
} from '../services/modelPriceService';

export const MODEL_PRICES_QUERY_KEY = ['ai-model-prices'];
export const UNPRICED_MODELS_QUERY_KEY = ['ai-unpriced-models'];
// A price edit changes the cost of past calls, so the usage screens refetch too.
const USAGE_QUERY_KEYS = [
  ['ai-usage-summary'],
  ['ai-usage-by-day'],
  ['ai-usage-top-users'],
  ['ai-usage-top-contacts'],
  ['ai-usage-recent'],
];

export function useModelPricesQuery() {
  return useQuery({ queryKey: MODEL_PRICES_QUERY_KEY, queryFn: listModelPrices });
}

export function useUnpricedModelsQuery() {
  return useQuery({ queryKey: UNPRICED_MODELS_QUERY_KEY, queryFn: listUnpricedModels });
}

function useInvalidatePrices() {
  const queryClient = useQueryClient();
  return () => {
    for (const key of [MODEL_PRICES_QUERY_KEY, UNPRICED_MODELS_QUERY_KEY, ...USAGE_QUERY_KEYS]) {
      queryClient.invalidateQueries({ queryKey: key });
    }
  };
}

export function useCreateModelPrice() {
  const invalidate = useInvalidatePrices();
  return useMutation({
    mutationFn: (input: ModelPriceInput) => createModelPrice(input),
    onSuccess: () => {
      invalidate();
      toast.success('Price saved');
    },
    onError: (error) => {
      toast.error(error instanceof Error ? error.message : 'Failed to save the price');
    },
  });
}

export function useUpdateModelPrice() {
  const invalidate = useInvalidatePrices();
  return useMutation({
    mutationFn: ({ id, input }: { id: string; input: ModelPriceInput }) =>
      updateModelPrice(id, input),
    onSuccess: () => {
      invalidate();
      toast.success('Price saved');
    },
    onError: (error) => {
      toast.error(error instanceof Error ? error.message : 'Failed to save the price');
    },
  });
}

/** Deferred hard delete (D7): `llm_model_price.delete` is parked server side. */
export function useModelPriceDeletion() {
  const deletion = useDeferredRowAction({
    actionKey: 'llm_model_price.delete',
    entityType: 'llm_model_price',
    successMessage: 'Price deleted',
    invalidateKeys: [MODEL_PRICES_QUERY_KEY, UNPRICED_MODELS_QUERY_KEY, ...USAGE_QUERY_KEYS],
  });
  const isRowPending = useCallback((id: string) => deletion.targetId === id, [deletion.targetId]);
  return { run: deletion.run, isRowPending, targetId: deletion.targetId };
}
