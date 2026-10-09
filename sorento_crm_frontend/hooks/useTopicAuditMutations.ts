'use client';

import { useMutation, useQueryClient } from '@tanstack/react-query';
import { toast } from '@/lib/toast';
import { TOPIC_AUDIT_KEY } from '@/hooks/useTopicAuditQuery';
import {
  drawTopicAuditSample,
  saveTopicAuditVerdict,
  type TopicAuditVerdictBody,
} from '@/services/topicAuditService';

export function useDrawTopicAuditSample() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (version: number) => drawTopicAuditSample(version),
    onSuccess: ({ drawn }) => {
      queryClient.invalidateQueries({ queryKey: [TOPIC_AUDIT_KEY] });
      toast.success(`${drawn} asks drawn`);
    },
    onError: (error) => {
      toast.error(
        error instanceof Error ? error.message : 'Failed to draw the sample',
      );
    },
  });
}

/** The error is left to the caller: the page shows it beside the rows. */
export function useSaveTopicAuditVerdict() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: TopicAuditVerdictBody }) =>
      saveTopicAuditVerdict(id, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: [TOPIC_AUDIT_KEY] });
    },
  });
}
