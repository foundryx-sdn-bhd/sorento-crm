'use client';

import { useQuery } from '@tanstack/react-query';
import {
  getTopicAudit,
  type TopicAudit,
  type TopicAuditQuery,
} from '@/services/topicAuditService';

export const TOPIC_AUDIT_KEY = 'topic-audit';

export function useTopicAuditQuery(query: TopicAuditQuery) {
  return useQuery<TopicAudit, Error>({
    queryKey: [TOPIC_AUDIT_KEY, query],
    queryFn: () => getTopicAudit(query),
    retry: 0,
  });
}
