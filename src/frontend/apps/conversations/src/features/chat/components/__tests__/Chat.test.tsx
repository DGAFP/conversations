/* eslint-disable testing-library/no-unnecessary-act, @typescript-eslint/require-await, testing-library/no-node-access */
import { ReadableStream } from 'node:stream/web';
import { TextDecoder, TextEncoder } from 'node:util';
import { deserialize, serialize } from 'node:v8';

import { CunninghamProvider } from '@gouvfr-lasuite/cunningham-react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Suspense } from 'react';
import { MemoryRouter } from 'react-router';
import type { Mock } from 'vitest';

import { fetchAPI } from '@/api';
import { ToastProvider } from '@/components/ToastProvider';
import { getConversation } from '@/features/chat/api/useConversation';
import { usePendingChatStore } from '@/features/chat/stores/usePendingChatStore';

import { Chat } from '../Chat';

// jsdom implements no scrolling; the component scrolls to the latest message.
Element.prototype.scrollTo = () => {};

// jsdom ships none of the globals the SDK uses to read a streamed response.
Object.assign(globalThis, {
  ReadableStream,
  TextDecoder,
  TextEncoder,
  structuredClone: <T,>(value: T): T => deserialize(serialize(value)) as T,
});

vi.mock('@/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api')>()),
  fetchAPI: vi.fn(),
}));

vi.mock('@/features/chat/api/useConversation', () => ({
  getConversation: vi.fn(),
  KEY_CONVERSATION: 'conversation',
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

// The markdown stack is ESM-only and irrelevant here: the assertions are about
// which messages are on screen, not how their text is rendered.
vi.mock('react-markdown', () => ({
  MarkdownHooks: ({ children }: { children: string }) => <div>{children}</div>,
}));
vi.mock('@shikijs/rehype/core', () => ({ default: () => {} }));
vi.mock('../../utils/shiki', () => ({
  getHighlighter: () => Promise.resolve({}),
}));
vi.mock('rehype-katex', () => ({ default: () => {} }));
vi.mock('remark-gfm', () => ({ default: () => {} }));
vi.mock('remark-math', () => ({ default: () => {} }));

const mockConfig: { ASSISTANT_RH_ENABLED?: boolean } = {};

vi.mock('@/core', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/core')>()),
  useConfig: () => ({ data: mockConfig }),
}));
vi.mock('@/core/config', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/core/config')>()),
  useConfig: () => ({ data: mockConfig }),
}));
vi.mock('@/features/chat/api/useAssistantHealth', () => ({
  useAssistantHealth: () => ({ data: undefined }),
}));
vi.mock('@/features/chat/api/useLLMConfiguration', () => ({
  useLLMConfiguration: () => ({ data: { models: [] } }),
}));
vi.mock('@/features/chat/api/useCreateConversation', () => ({
  useCreateChatConversation: () => ({ mutate: vi.fn() }),
}));
vi.mock('@/features/attachments/api/useProjectAttachments', () => ({
  useProjectAttachments: () => ({ data: undefined }),
}));
vi.mock('@/features/attachments/api/useReindexProjectAttachment', () => ({
  useReindexProjectAttachment: () => ({ mutate: vi.fn(), isPending: false }),
}));
vi.mock('@/features/attachments/hooks/useUploadFile', () => ({
  useUploadFile: () => ({
    uploadFile: vi.fn(),
    isErrorAttachment: false,
    errorAttachment: undefined,
  }),
}));
vi.mock('@/features/sources-panel', () => ({
  useSourcePanelAnchor: () => document.getElementById('test-sources-panel'),
  SourcePanel: () => null,
}));

const ANSWER_STREAM = [
  'data: {"type":"start"}\n\n',
  'data: {"type":"text-start","id":"t1"}\n\n',
  'data: {"type":"text-delta","id":"t1","delta":"An answer."}\n\n',
  'data: {"type":"text-end","id":"t1"}\n\n',
  'data: {"type":"finish"}\n\n',
  'data: [DONE]\n\n',
].join('');

const streamOf = (payload: string) =>
  new ReadableStream({
    start(controller) {
      controller.enqueue(new TextEncoder().encode(payload));
      controller.close();
    },
  });

const HISTORY = [
  {
    id: 'server-u1',
    role: 'user' as const,
    parts: [{ type: 'text' as const, text: 'An older question' }],
  },
  {
    id: 'server-a1',
    role: 'assistant' as const,
    parts: [{ type: 'text' as const, text: 'An older answer' }],
  },
];

const renderChat = (conversationId: string | undefined = 'conv-1') =>
  render(
    <MemoryRouter>
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <CunninghamProvider>
          <ToastProvider>
            <Suspense fallback={null}>
              <Chat initialConversationId={conversationId} />
            </Suspense>
          </ToastProvider>
        </CunninghamProvider>
      </QueryClientProvider>
    </MemoryRouter>,
  );

const chatPostCount = (mock: Mock) =>
  mock.mock.calls.filter((call) => String(call[0]).includes('/conversation/'))
    .length;

const messageTexts = () =>
  [...document.querySelectorAll('[data-message-id]')].map((el) =>
    el.textContent?.replace(/\s+/g, ' ').trim(),
  );

const ask = async (text: string) => {
  const box = screen.getByRole('textbox');
  await userEvent.type(box, text);
  await userEvent.keyboard('{Enter}');
};

describe('Chat message ownership', () => {
  const fetchAPIMock = vi.mocked(fetchAPI) as unknown as Mock;
  const getConversationMock = vi.mocked(getConversation) as unknown as Mock;

  beforeEach(() => {
    vi.clearAllMocks();
    usePendingChatStore.setState({ input: '', files: null });
    fetchAPIMock.mockImplementation((url: string) => {
      if (url.startsWith('chat-cooldown')) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ cooldown_seconds: 0 }),
        });
      }
      return Promise.resolve({ ok: true, body: streamOf(ANSWER_STREAM) });
    });
  });

  it('keeps the question on screen when a stale snapshot resolves mid-turn', async () => {
    // The real new-conversation handoff: the component auto-submits the carried
    // message, clears the pending input, and that re-runs the effect below.
    // Its snapshot was taken before the message was stored, and applying it
    // used to wipe the question until the next reload.
    usePendingChatStore.setState({ input: 'Carried question' });
    getConversationMock.mockResolvedValue({ messages: [] });

    renderChat();

    await waitFor(() => expect(getConversationMock).toHaveBeenCalled());
    await waitFor(() =>
      expect(messageTexts()).toEqual([
        'You said: Carried question',
        'Assistant IA replied: An answer.',
      ]),
    );
  });

  it('keeps the question on screen when the history request fails', async () => {
    // Same rule as a resolved snapshot: a failed refetch must not clear a
    // conversation the client has already sent into.
    usePendingChatStore.setState({ input: 'Carried question' });
    getConversationMock.mockRejectedValue(new Error('boom'));

    renderChat();

    await waitFor(() => expect(getConversationMock).toHaveBeenCalled());
    await waitFor(() =>
      expect(messageTexts()).toEqual([
        'You said: Carried question',
        'Assistant IA replied: An answer.',
      ]),
    );
  });

  it('sends once when submitted twice before the history resolves', async () => {
    // Submission waits for the in-flight history fetch, and until a send
    // actually starts the status stays `ready`, so the composer still accepts
    // Enter. Both submissions would otherwise resume on the same input.
    let resolveFetch: (value: { messages: [] }) => void = () => {};
    getConversationMock.mockReturnValue(
      new Promise((resolve) => {
        resolveFetch = resolve;
      }),
    );

    renderChat();

    const box = screen.getByRole('textbox');
    await userEvent.type(box, 'Double send');
    await userEvent.keyboard('{Enter}');
    await userEvent.keyboard('{Enter}');

    await act(async () => {
      resolveFetch({ messages: [] });
    });

    await waitFor(() =>
      expect(messageTexts()).toEqual([
        'You said: Double send',
        'Assistant IA replied: An answer.',
      ]),
    );
    expect(chatPostCount(fetchAPIMock)).toBe(1);
  });

  it('replaces only the failed turn when retrying', async () => {
    // Retry used to remove the last assistant message, which is the previous
    // successful answer whenever the attempt failed before producing one, and
    // it left the question on screen twice.
    getConversationMock.mockResolvedValue({ messages: HISTORY });

    renderChat();
    await waitFor(() =>
      expect(messageTexts()).toEqual([
        'You said: An older question',
        'Assistant IA replied: An older answer',
      ]),
    );

    const cooldown = {
      ok: true,
      json: () => Promise.resolve({ cooldown_seconds: 0 }),
    };
    fetchAPIMock.mockImplementation((url: string) =>
      url.startsWith('chat-cooldown')
        ? Promise.resolve(cooldown)
        : Promise.resolve({ ok: false, status: 500 }),
    );

    await act(async () => {
      await ask('Second question');
    });

    const retry = await screen.findByRole('button', { name: 'Retry' });

    fetchAPIMock.mockImplementation((url: string) =>
      url.startsWith('chat-cooldown')
        ? Promise.resolve(cooldown)
        : Promise.resolve({ ok: true, body: streamOf(ANSWER_STREAM) }),
    );

    await act(async () => {
      await userEvent.click(retry);
    });

    await waitFor(() =>
      expect(messageTexts()).toEqual([
        'You said: An older question',
        'Assistant IA replied: An older answer',
        'You said: Second question',
        'Assistant IA replied: An answer.',
      ]),
    );
  });

  it('applies the fetched history even when a message is sent while it loads', async () => {
    // Switching conversations clears the messages and fetches the new history.
    // Sending inside that window used to leave the history unapplied, so the
    // conversation looked empty apart from the new exchange until a reload.
    let resolveFetch: (value: { messages: typeof HISTORY }) => void = () => {};
    getConversationMock.mockReturnValue(
      new Promise((resolve) => {
        resolveFetch = resolve;
      }),
    );

    renderChat();

    // The submission is fully initiated while the fetch is still in flight.
    await act(async () => {
      await ask('Sent while loading');
    });

    await act(async () => {
      resolveFetch({ messages: HISTORY });
    });

    await waitFor(() =>
      expect(messageTexts()).toEqual([
        'You said: An older question',
        'Assistant IA replied: An older answer',
        'You said: Sent while loading',
        'Assistant IA replied: An answer.',
      ]),
    );
  });
});

describe('RH sources panel', () => {
  const sourceMessage = {
    id: 'rh-answer',
    role: 'assistant' as const,
    parts: [{ type: 'text' as const, text: 'Réponse RH.' }],
    metadata: {
      assistant_rh: {
        sources: [
          {
            title: 'Guide RH',
            publisher: 'DGAFP',
            access: 'authenticated',
            url: '/api/v1.0/chats/conv-1/messages/rh-answer/sources/0/',
          },
          {
            title: 'Article public',
            publisher: 'Service-Public',
            access: 'public',
            url: 'https://www.service-public.fr/particuliers/vosdroits/F1',
          },
        ],
      },
    },
  };

  beforeEach(() => {
    vi.clearAllMocks();
    mockConfig.ASSISTANT_RH_ENABLED = true;
    usePendingChatStore.setState({ input: '', files: null });
    const anchor = document.createElement('div');
    anchor.id = 'test-sources-panel';
    document.body.appendChild(anchor);
    vi.mocked(getConversation).mockResolvedValue({
      messages: [sourceMessage],
    } as Awaited<ReturnType<typeof getConversation>>);
  });

  afterEach(() => {
    mockConfig.ASSISTANT_RH_ENABLED = false;
    document.getElementById('test-sources-panel')?.remove();
  });

  it('opens restored sources in the panel and closes it with its close button', async () => {
    renderChat();
    const toggle = await screen.findByRole('button', {
      name: 'Show 2 sources',
    });
    expect(
      screen.queryByRole('link', { name: 'Guide RH — nouvel onglet' }),
    ).not.toBeInTheDocument();
    await userEvent.click(toggle);
    expect(
      screen.getByRole('heading', { name: '2 Sources' }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('link', { name: 'Guide RH — nouvel onglet' }),
    ).toHaveAttribute(
      'href',
      expect.stringContaining('/messages/rh-answer/sources/0/'),
    );
    expect(
      screen.getByRole('link', { name: 'Article public — nouvel onglet' }),
    ).toHaveAttribute('target', '_blank');
    await userEvent.click(
      screen.getByRole('button', { name: 'Close sources panel' }),
    );
    expect(
      screen.queryByRole('link', { name: 'Guide RH — nouvel onglet' }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'Show 2 sources' }),
    ).toBeInTheDocument();
  });

  it('switches the panel to the selected answer and toggles it closed', async () => {
    vi.mocked(getConversation).mockResolvedValue({
      messages: [
        sourceMessage,
        {
          ...sourceMessage,
          id: 'other-answer',
          metadata: {
            assistant_rh: {
              sources: [
                {
                  ...sourceMessage.metadata.assistant_rh.sources[1],
                  title: 'Autre article',
                },
              ],
            },
          },
        },
      ],
    } as Awaited<ReturnType<typeof getConversation>>);
    renderChat();
    await userEvent.click(
      await screen.findByRole('button', { name: 'Show 2 sources' }),
    );
    await userEvent.click(
      screen.getByRole('button', { name: 'Show 1 source' }),
    );
    expect(
      screen.getByRole('heading', { name: '1 Source' }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('link', { name: 'Autre article — nouvel onglet' }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole('link', { name: 'Guide RH — nouvel onglet' }),
    ).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Hide source' }));
    expect(
      screen.queryByRole('heading', { name: '1 Source' }),
    ).not.toBeInTheDocument();
  });

  it('shows the unavailable state for legacy answers inside the panel', async () => {
    vi.mocked(getConversation).mockResolvedValue({
      messages: [{ ...sourceMessage, metadata: undefined }],
    } as Awaited<ReturnType<typeof getConversation>>);
    renderChat();
    await userEvent.click(
      await screen.findByRole('button', { name: 'Show sources' }),
    );
    expect(screen.getByRole('status')).toHaveTextContent(
      'Références documentaires indisponibles',
    );
    expect(screen.queryByRole('link')).not.toBeInTheDocument();
  });
});
