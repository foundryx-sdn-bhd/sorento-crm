import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from '@/lib/toast';
import {
  createRole,
  deleteRole,
  getContactAccess,
  getRegistry,
  getRole,
  listRoleContacts,
  listRoles,
  setContactAccess,
  setRoleGrants,
  updateRole,
  type ContactAccessInput,
  type RoleGrants,
  type RoleInput,
} from '@/services/chatbotAccessService';

export const chatbotAccessKeys = {
  registry: ['chatbot-access', 'registry'] as const,
  roles: ['chatbot-access', 'roles'] as const,
  role: (id: string) => ['chatbot-access', 'role', id] as const,
  roleContacts: (id: string) => ['chatbot-access', 'role-contacts', id] as const,
  contact: (id: string) => ['chatbot-access', 'contact', id] as const,
};

export function useChatbotRegistry() {
  return useQuery({ queryKey: chatbotAccessKeys.registry, queryFn: getRegistry });
}

export function useChatbotRoles() {
  return useQuery({ queryKey: chatbotAccessKeys.roles, queryFn: listRoles });
}

export function useChatbotRole(id: string) {
  return useQuery({
    queryKey: chatbotAccessKeys.role(id),
    queryFn: () => getRole(id),
    enabled: !!id,
  });
}

export function useChatbotRoleContacts(id: string) {
  return useQuery({
    queryKey: chatbotAccessKeys.roleContacts(id),
    queryFn: () => listRoleContacts(id),
    enabled: !!id,
  });
}

export function useContactAccess(contactId: string) {
  return useQuery({
    queryKey: chatbotAccessKeys.contact(contactId),
    queryFn: () => getContactAccess(contactId),
    enabled: !!contactId,
  });
}

function useInvalidateAccess() {
  const queryClient = useQueryClient();
  return () => queryClient.invalidateQueries({ queryKey: ['chatbot-access'] });
}

export function useCreateChatbotRole() {
  const invalidate = useInvalidateAccess();
  return useMutation({
    mutationFn: (input: RoleInput) => createRole(input),
    onSuccess: () => {
      invalidate();
      toast.success('Role created');
    },
    onError: (e: Error) => toast.error(e.message),
  });
}

export function useUpdateChatbotRole(id: string) {
  const invalidate = useInvalidateAccess();
  return useMutation({
    mutationFn: (input: Partial<RoleInput>) => updateRole(id, input),
    onSuccess: () => {
      invalidate();
      toast.success('Role updated');
    },
    onError: (e: Error) => toast.error(e.message),
  });
}

export function useDeleteChatbotRole() {
  const invalidate = useInvalidateAccess();
  return useMutation({
    mutationFn: (id: string) => deleteRole(id),
    onSuccess: () => {
      invalidate();
      toast.success('Role deleted');
    },
    onError: (e: Error) => toast.error(e.message),
  });
}

export function useSetRoleGrants(id: string) {
  const invalidate = useInvalidateAccess();
  return useMutation({
    mutationFn: (grants: RoleGrants) => setRoleGrants(id, grants),
    onSuccess: () => {
      invalidate();
      toast.success('Role saved');
    },
    onError: (e: Error) => toast.error(e.message),
  });
}

export function useSetContactAccess(contactId: string) {
  const invalidate = useInvalidateAccess();
  return useMutation({
    mutationFn: (input: ContactAccessInput) => setContactAccess(contactId, input),
    onSuccess: () => {
      invalidate();
      toast.success('Access saved');
    },
    onError: (e: Error) => toast.error(e.message),
  });
}
