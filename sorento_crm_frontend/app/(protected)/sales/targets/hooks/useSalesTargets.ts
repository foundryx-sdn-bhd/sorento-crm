import { useCallback } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from '@/lib/toast';
import { LIST_QUERY_OPTIONS } from '@/lib/list-query/options';
import {
  createSalesTarget,
  createTargetChild,
  duplicateSalesTarget,
  getPointRatios,
  getSalesTarget,
  getSalesTargetOptions,
  getSalesTargets,
  importDealerTargets,
  patchSalesTarget,
  patchSalesTargetPeriod,
  savePointRatio,
  searchTargetCustomers,
  searchTargetProducts,
} from '../services/salesTargetService';
import type {
  SalesTargetCreatePayload,
  SalesTargetListParams,
  SalesTargetUpdatePayload,
} from '../types/salesTarget.types';

export const SALES_TARGETS_KEY = ['sales-targets'] as const;
export const SALES_TARGET_KEY = ['sales-target'] as const;
export const SALES_TARGET_OPTIONS_KEY = ['sales-target-options'] as const;
export const POINT_RATIOS_KEY = ['sales-target-point-ratios'] as const;
/** The Sales Teams list carries "Targets now", so a target write refreshes it too. */
const SALES_TEAMS_KEY = ['sales-teams'] as const;

export function useSalesTargets(params: SalesTargetListParams, enabled = true) {
  return useQuery({
    ...LIST_QUERY_OPTIONS,
    queryKey: [...SALES_TARGETS_KEY, params],
    queryFn: () => getSalesTargets(params),
    enabled,
  });
}

export function useSalesTarget(id: string | null, on?: string) {
  return useQuery({
    queryKey: [...SALES_TARGET_KEY, id, on ?? null],
    queryFn: () => getSalesTarget(id as string, on),
    enabled: !!id,
    retry: 1,
  });
}

export function useSalesTargetOptions(enabled = true) {
  return useQuery({
    queryKey: SALES_TARGET_OPTIONS_KEY,
    queryFn: getSalesTargetOptions,
    enabled,
    staleTime: 30_000,
  });
}

/** The scope picker's product search, paged on the server. */
export function useTargetProductSearch() {
  return useCallback((query: string) => searchTargetProducts(query), []);
}

function useInvalidateTargets() {
  const queryClient = useQueryClient();
  return () => {
    queryClient.invalidateQueries({ queryKey: SALES_TARGETS_KEY });
    queryClient.invalidateQueries({ queryKey: SALES_TARGET_KEY });
    queryClient.invalidateQueries({ queryKey: SALES_TEAMS_KEY });
  };
}

export function useCreateSalesTarget() {
  const invalidate = useInvalidateTargets();
  return useMutation({
    mutationFn: (payload: SalesTargetCreatePayload) => createSalesTarget(payload),
    onSuccess: (target) => {
      invalidate();
      toast.success(`Target set: ${target.name}`);
    },
    onError: (error: Error) => toast.error(error.message || 'Failed to set target'),
  });
}

export function usePatchSalesTarget() {
  const invalidate = useInvalidateTargets();
  return useMutation({
    mutationFn: ({ targetId, ...payload }: SalesTargetUpdatePayload & { targetId: string }) =>
      patchSalesTarget(targetId, payload),
    onSuccess: () => {
      invalidate();
      toast.success('Target saved');
    },
    onError: (error: Error) => toast.error(error.message || 'Failed to save target'),
  });
}

export function usePatchSalesTargetPeriod() {
  const invalidate = useInvalidateTargets();
  return useMutation({
    mutationFn: ({ targetId, periodId, target_value }: { targetId: string; periodId: string; target_value: number }) =>
      patchSalesTargetPeriod(targetId, periodId, { target_value }),
    onSuccess: () => {
      invalidate();
      toast.success('Figure saved');
    },
    onError: (error: Error) => toast.error(error.message || 'Failed to save the figure'),
  });
}

export function useCreateTargetChild() {
  const invalidate = useInvalidateTargets();
  return useMutation({
    mutationFn: ({
      targetId,
      sales_agent_id,
      target_value,
    }: {
      targetId: string;
      sales_agent_id: string;
      target_value: number;
    }) => createTargetChild(targetId, { sales_agent_id, target_value }),
    onSuccess: () => {
      invalidate();
      toast.success('Figure added');
    },
    onError: (error: Error) => toast.error(error.message || 'Failed to add the figure'),
  });
}

export function useDuplicateSalesTarget() {
  const invalidate = useInvalidateTargets();
  return useMutation({
    mutationFn: (targetId: string) => duplicateSalesTarget(targetId),
    onSuccess: (copy) => {
      invalidate();
      toast.success(`Duplicated as ${copy.target_no}`);
    },
    onError: (error: Error) => toast.error(error.message || 'Failed to duplicate target'),
  });
}

/** A dealer target's account picker: customers searched on the server. */
export function useTargetCustomerSearch() {
  return useCallback((query: string) => searchTargetCustomers(query), []);
}

export function usePointRatios(enabled = true) {
  return useQuery({ queryKey: POINT_RATIOS_KEY, queryFn: getPointRatios, enabled });
}

export function useSavePointRatio() {
  const queryClient = useQueryClient();
  const invalidate = useInvalidateTargets();
  return useMutation({
    mutationFn: savePointRatio,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: POINT_RATIOS_KEY });
      invalidate();
      toast.success('Ratio saved');
    },
    onError: (error: Error) => toast.error(error.message || 'Failed to save the ratio'),
  });
}

export function useImportDealerTargets() {
  const invalidate = useInvalidateTargets();
  return useMutation({
    mutationFn: ({ file, dryRun, year }: { file: File; dryRun: boolean; year: number }) =>
      importDealerTargets(file, dryRun, year),
    onSuccess: (result) => {
      if (result.dry_run) return;
      invalidate();
      toast.success(
        `Imported: ${result.summary.targets_new} new, ${result.summary.targets_updated} updated`,
      );
    },
    onError: (error: Error) => toast.error(error.message || 'Failed to import the file'),
  });
}
