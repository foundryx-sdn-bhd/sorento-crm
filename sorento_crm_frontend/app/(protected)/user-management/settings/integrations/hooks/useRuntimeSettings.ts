import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from '@/lib/toast';

import {
  getRuntimeSettings,
  saveRuntimeSettings,
  type RuntimeSettingsUpdate,
} from '../services/runtimeSettingsService';

export const RUNTIME_SETTINGS_KEY = ['runtime-settings'];

export function useRuntimeSettings() {
  return useQuery({
    queryKey: RUNTIME_SETTINGS_KEY,
    queryFn: getRuntimeSettings,
    retry: 1,
    // The cards render the failure in place with a Retry; no toast on top of it.
    meta: { silent: true },
  });
}

export function useSaveRuntimeSettings() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (update: RuntimeSettingsUpdate) => saveRuntimeSettings(update),
    onSuccess: (data) => {
      queryClient.setQueryData(RUNTIME_SETTINGS_KEY, data);
      toast.success('Integration settings saved');
    },
    onError: (error: Error) =>
      toast.error(error.message || 'Failed to save settings'),
  });
}
