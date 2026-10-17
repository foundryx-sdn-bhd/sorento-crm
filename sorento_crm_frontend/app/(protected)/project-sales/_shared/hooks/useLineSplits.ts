'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from '@/lib/toast';
import { getLineSplits, saveLineSplits } from '../services/lineSplitService';
import type { LineSplitsSaveBody } from '../types/lineSplit.types';
import {
  FULFILMENT_PLANNING_KEY,
  PLANNING_BOARD_KEY,
} from './useFulfilmentPlanning';
import {
  ORDER_INQUIRY_HEADER_LINES_KEY,
  ORDER_INQUIRY_WORKLIST_KEY,
} from './useOrderInquiry';

export const LINE_SPLITS_KEY = 'project-so-line-splits';

/** OI-LINE-SPLIT: the whole-SO split view's data and its one Save. */
export function useLineSplits(
  psoId: string | null | undefined,
  enabled = true,
) {
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: [LINE_SPLITS_KEY, psoId ?? ''],
    queryFn: () => getLineSplits(psoId as string),
    enabled: Boolean(psoId) && enabled,
  });
  const save = useMutation({
    mutationFn: (body: LineSplitsSaveBody) =>
      saveLineSplits(psoId as string, body),
    onSuccess: (data) => {
      queryClient.setQueryData([LINE_SPLITS_KEY, psoId ?? ''], data);
      void queryClient.invalidateQueries({ queryKey: [PLANNING_BOARD_KEY] });
      void queryClient.invalidateQueries({
        queryKey: [FULFILMENT_PLANNING_KEY],
      });
      void queryClient.invalidateQueries({
        queryKey: [ORDER_INQUIRY_WORKLIST_KEY],
      });
      void queryClient.invalidateQueries({
        queryKey: [ORDER_INQUIRY_HEADER_LINES_KEY],
      });
      toast.success('Splits saved');
    },
    onError: (error: Error) => {
      toast.error(error.message || 'Failed to save the splits');
    },
  });
  return { query, save };
}
