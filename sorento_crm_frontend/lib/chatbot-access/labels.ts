import type { AudienceTier } from '@/services/chatbotAccessService';

export const TIER_LABEL: Record<AudienceTier, string> = {
  dealer: 'Dealer',
  office: 'Office',
  end_user: 'End user',
};

export const TIER_OPTIONS = (Object.keys(TIER_LABEL) as AudienceTier[]).map((value) => ({
  value,
  label: TIER_LABEL[value],
}));

export const tierLabel = (tier?: AudienceTier | null) => (tier ? TIER_LABEL[tier] : '');
