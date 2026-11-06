import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { APIError, errorCauses, fetchAPI } from '@/api';

export interface AssistantRHFeedback {
  stars: number;
  reasons_positive: string[];
  reasons_negative: string[];
  comment: string;
}

export function useAssistantRHFeedback(
  conversationId: string,
  messageId: string,
) {
  const queryClient = useQueryClient();
  const path = `chats/${encodeURIComponent(conversationId)}/messages/${encodeURIComponent(messageId)}/feedback/`;
  const queryKey = ['assistant-rh-feedback', path];
  const query = useQuery<AssistantRHFeedback | null, APIError>({
    queryKey,
    queryFn: async ({ signal }) => {
      const response = await fetchAPI(path, { signal });
      if (response.status === 204) return null;
      if (!response.ok) {
        throw new APIError(
          'Failed to load feedback',
          await errorCauses(response),
        );
      }
      return response.json() as Promise<AssistantRHFeedback | null>;
    },
  });
  const mutation = useMutation({
    onMutate: async () => {
      await queryClient.cancelQueries({ queryKey, exact: true });
    },
    mutationFn: async (feedback: AssistantRHFeedback) => {
      const response = await fetchAPI(path, {
        method: 'POST',
        body: JSON.stringify(feedback),
      });
      if (!response.ok) {
        throw new APIError(
          'Failed to send feedback',
          await errorCauses(response),
        );
      }
    },
    onSuccess: async (_result, feedback) => {
      await queryClient.cancelQueries({ queryKey, exact: true });
      queryClient.setQueryData(queryKey, feedback);
    },
  });
  return { query, mutation };
}
