import { CunninghamProvider } from '@gouvfr-lasuite/cunningham-react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { AssistantRHFeedback as FeedbackValue } from '@/features/chat/api/useAssistantRHFeedback';
import '@/i18n/initI18n';

import { AssistantRHFeedback } from '../AssistantRHFeedback';

const fetchMock = vi.fn<typeof fetch>();
const endpoint = '/api/v1.0/chats/conversation/messages/answer/feedback/';

function setup(saved: FeedbackValue | null = null, readError = false) {
  fetchMock.mockImplementation((_url, init) => {
    if (init?.method === 'POST')
      return Promise.resolve(new Response(null, { status: 204 }));
    if (readError) return Promise.resolve(new Response('{}', { status: 503 }));
    return Promise.resolve(
      saved
        ? new Response(JSON.stringify(saved), { status: 200 })
        : new Response(null, { status: 204 }),
    );
  });
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const view = render(
    <QueryClientProvider client={client}>
      <CunninghamProvider theme="dsfr">
        <AssistantRHFeedback conversationId="conversation" messageId="answer" />
      </CunninghamProvider>
    </QueryClientProvider>,
  );
  return { ...view, client, user: userEvent.setup() };
}

async function rate(user: ReturnType<typeof userEvent.setup>, value: number) {
  const radio = screen.getByRole('radio', {
    name: new RegExp(`^${value} étoile`),
  });
  await waitFor(() => expect(radio).toBeEnabled());
  await user.click(radio);
}

describe('AssistantRHFeedback', () => {
  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal('fetch', fetchMock);
  });
  afterEach(() => vi.unstubAllGlobals());

  it.each([1, 2, 3, 4, 5])(
    'opens the appropriate reasons for %i stars without sending',
    async (value) => {
      const { user } = setup();
      await rate(user, value);
      expect(screen.getByRole('form')).toBeVisible();
      expect(screen.queryByRole('checkbox', { name: 'Clair' }) !== null).toBe(
        value >= 3,
      );
      expect(
        screen.queryByRole('checkbox', { name: 'Incomplet' }) !== null,
      ).toBe(value <= 4);
      expect(
        fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST'),
      ).toHaveLength(0);
    },
  );

  it('requires a reason or nonblank comment and sends the exact API fields', async () => {
    const { user } = setup();
    await rate(user, 4);
    await user.click(
      screen.getByRole('button', { name: 'Envoyer mon évaluation' }),
    );
    expect(screen.getByRole('alert')).toHaveTextContent(
      'sélectionnez un motif',
    );
    await user.click(screen.getByRole('checkbox', { name: 'Clair' }));
    await user.click(screen.getByRole('checkbox', { name: 'Incomplet' }));
    await user.type(
      screen.getByRole('textbox'),
      '  Un exemple serait utile.  ',
    );
    await user.click(
      screen.getByRole('button', { name: 'Envoyer mon évaluation' }),
    );
    await screen.findByText('Merci pour votre retour');
    const [url, init] = fetchMock.mock.calls.find(
      ([, options]) => options?.method === 'POST',
    )!;
    expect(url).toContain(endpoint);
    expect(init?.credentials).toBe('include');
    expect(JSON.parse(init?.body as string)).toEqual({
      stars: 4,
      reasons_positive: ['Clair'],
      reasons_negative: ['Incomplet'],
      comment: 'Un exemple serait utile.',
    });
    expect(screen.queryByRole('form')).not.toBeInTheDocument();
  });

  it('discards incompatible reasons when changing the rating', async () => {
    const { user } = setup();
    await rate(user, 3);
    await user.click(screen.getByRole('checkbox', { name: 'Clair' }));
    await user.click(screen.getByRole('checkbox', { name: 'Incomplet' }));
    await rate(user, 5);
    expect(
      screen.queryByRole('checkbox', { name: 'Incomplet' }),
    ).not.toBeInTheDocument();
    await rate(user, 2);
    expect(
      screen.getByRole('checkbox', { name: 'Incomplet' }),
    ).not.toBeChecked();
    await rate(user, 3);
    expect(screen.getByRole('checkbox', { name: 'Clair' })).not.toBeChecked();
  });

  it('retains the draft after failure and confirms only after a successful retry', async () => {
    const { user } = setup();
    await rate(user, 1);
    await user.type(
      screen.getByRole('textbox'),
      'La réponse ne traite pas la question.',
    );
    fetchMock.mockResolvedValueOnce(new Response('{}', { status: 503 }));
    await user.click(
      screen.getByRole('button', { name: 'Envoyer mon évaluation' }),
    );
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'n’a pas pu être enregistrée',
    );
    expect(screen.getByRole('textbox')).toHaveValue(
      'La réponse ne traite pas la question.',
    );
    expect(
      screen.queryByText('Merci pour votre retour'),
    ).not.toBeInTheDocument();
    await user.click(
      screen.getByRole('button', { name: 'Envoyer mon évaluation' }),
    );
    await screen.findByText('Merci pour votre retour');
  });

  it('loads a saved evaluation and lets the user edit or cancel it', async () => {
    const saved = {
      stars: 5,
      reasons_positive: ['Précis'],
      reasons_negative: [],
      comment: 'Merci',
    };
    const { user } = setup(saved);
    await screen.findByText('Merci pour votre retour');
    expect(screen.getByRole('radio', { name: /^5 étoiles/ })).toBeChecked();
    await user.click(
      screen.getByRole('button', { name: 'Modifier mon évaluation' }),
    );
    expect(screen.getByRole('checkbox', { name: 'Précis' })).toBeChecked();
    expect(screen.getByRole('textbox')).toHaveValue('Merci');
    await rate(user, 1);
    await user.click(screen.getByRole('button', { name: 'Annuler' }));
    expect(screen.getByRole('radio', { name: /^5 étoiles/ })).toBeChecked();
    expect(screen.queryByRole('form')).not.toBeInTheDocument();
  });

  it('supports keyboard navigation and does not send a feedback just by rating', async () => {
    const { user } = setup();
    await waitFor(() => expect(screen.getAllByRole('radio')[0]).toBeEnabled());
    await user.tab();
    await user.keyboard('[Space][ArrowRight][ArrowRight]');
    expect(screen.getByRole('radio', { name: /^3 étoiles/ })).toBeChecked();
    expect(screen.getByRole('form')).toBeVisible();
  });

  it('does not replace a failed read with a new evaluation and allows retry', async () => {
    const { user } = setup(null, true);
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Impossible de charger',
    );
    expect(screen.getAllByRole('radio')[0]).toBeDisabled();
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }));
    await user.click(screen.getByRole('button', { name: 'Réessayer' }));
    await waitFor(() => expect(screen.getAllByRole('radio')[0]).toBeEnabled());
    await rate(user, 3);
    expect(screen.getByRole('form')).toBeVisible();
  });

  it.each(['before', 'during'])(
    'keeps the saved feedback when an older read starts %s the POST',
    async (timing) => {
      const saved = {
        stars: 1,
        reasons_positive: [],
        reasons_negative: ['Confus'],
        comment: '',
      };
      const { user, client } = setup(saved);
      await screen.findByText('Merci pour votre retour');
      await rate(user, 5);
      await user.click(screen.getByRole('checkbox', { name: 'Clair' }));
      let resolveRead!: (response: Response) => void;
      let resolveSave!: (response: Response) => void;
      let readSignal: AbortSignal | null | undefined;
      fetchMock.mockImplementation((_url, init) => {
        if (init?.method === 'POST')
          return new Promise<Response>((resolve) => {
            resolveSave = resolve;
          });
        readSignal = init?.signal;
        return new Promise<Response>((resolve) => {
          resolveRead = resolve;
        });
      });
      const refetch = async () => {
        await act(() =>
          Promise.resolve().then(() => {
            void client.refetchQueries({ queryKey: ['assistant-rh-feedback'] });
          }),
        );
      };
      if (timing === 'before') await refetch();
      await user.click(
        screen.getByRole('button', { name: 'Envoyer mon évaluation' }),
      );
      await waitFor(() => expect(typeof resolveSave).toBe('function'));
      if (timing === 'during') await refetch();
      await act(() =>
        Promise.resolve().then(() => {
          resolveSave(new Response(null, { status: 204 }));
        }),
      );
      await screen.findByText('Merci pour votre retour');
      expect(readSignal?.aborted).toBe(true);
      await act(() =>
        Promise.resolve().then(() => {
          resolveRead(new Response(JSON.stringify(saved), { status: 200 }));
        }),
      );
      expect(screen.getByRole('radio', { name: /^5 étoiles/ })).toBeChecked();
      await user.click(
        screen.getByRole('button', { name: 'Modifier mon évaluation' }),
      );
      expect(screen.getByRole('checkbox', { name: 'Clair' })).toBeChecked();
    },
  );
});
