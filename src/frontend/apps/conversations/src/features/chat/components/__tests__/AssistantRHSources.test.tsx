import { render, screen } from '@testing-library/react';
import { UIMessage } from 'ai';

import { AssistantRHSources } from '../AssistantRHSources';

vi.mock('react-router', async (importOriginal) => ({
  ...(await importOriginal<typeof import('react-router')>()),
  useNavigate: () => vi.fn(),
}));

const path =
  '/api/v1.0/chats/11111111-1111-4111-8111-111111111111/messages/message-1/sources/0/';
const source = {
  title: 'Guide des contractuels',
  publisher: 'DGAFP',
  url: path,
  access: 'authenticated',
};

function message(sources: unknown[]): UIMessage {
  return {
    id: 'message-1',
    role: 'assistant',
    parts: [{ type: 'text', text: 'Réponse RH' }],
    metadata: { assistant_rh: { status: 'available', sources } },
  };
}

describe('Assistant RH selected documents', () => {
  it('renders one protected source link opening in a new tab without a preview', () => {
    render(<AssistantRHSources message={message([source])} />);
    expect(screen.getByText('DGAFP')).toBeInTheDocument();
    const link = screen.getByRole('link', {
      name: 'Guide des contractuels — nouvel onglet',
    });
    expect(link.getAttribute('href')).toContain(path);
    expect(link).toHaveAttribute('target', '_blank');
    expect(link).toHaveAttribute('rel', 'noopener noreferrer');
    expect(link).toHaveTextContent(source.title);
    expect(screen.getAllByRole('link')).toHaveLength(1);
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
    expect(screen.queryByTitle(/Aperçu PDF/)).not.toBeInTheDocument();
  });

  it('preserves canonical public links without embedding external sites', () => {
    render(
      <AssistantRHSources
        message={message([
          {
            ...source,
            access: 'public',
            url: 'https://www.service-public.fr/particuliers/vosdroits/F1',
          },
        ])}
      />,
    );
    expect(screen.getByRole('link')).toHaveAttribute(
      'href',
      'https://www.service-public.fr/particuliers/vosdroits/F1',
    );
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });

  it('shows an explicit state for legacy responses and rejects storage URLs', () => {
    const { rerender } = render(<AssistantRHSources />);
    expect(screen.getByRole('status')).toHaveTextContent(
      'Références documentaires indisponibles',
    );
    rerender(
      <AssistantRHSources
        message={message([
          { ...source, url: 'https://bucket.test/private.pdf?secret=x' },
        ])}
      />,
    );
    expect(screen.queryByRole('link')).not.toBeInTheDocument();
    expect(screen.getByText('Document indisponible.')).toBeInTheDocument();
  });

  it.each([
    [
      'Légifrance',
      'Article L332-1 — Code du travail',
      'https://www.legifrance.gouv.fr/codes/article_lc/LEGIARTI123',
      'DGAFP',
    ],
    [
      'Légifrance',
      'Article 1 — Décret du 17 janvier 1986',
      'https://legifrance.gouv.fr/loda/article_lc/LEGIARTI456',
      '',
    ],
    [
      'Service-Public',
      "Congés annuels d'un agent contractuel",
      'https://www.service-public.gouv.fr/particuliers/vosdroits/F1',
      'Service-Public (Scaleway)',
    ],
    [
      'Service-Public',
      "Congés annuels d'un agent contractuel",
      'https://www.service-public.fr/particuliers/vosdroits/F1',
      '',
    ],
  ])(
    'renders %s as the source type and the article name as the title',
    (publisher, title, url, storedPublisher) => {
      const restored = JSON.parse(
        JSON.stringify(
          message([
            { title, publisher: storedPublisher, url, access: 'public' },
          ]),
        ),
      ) as UIMessage;
      render(<AssistantRHSources message={restored} />);
      const type = screen.getByText(publisher);
      expect(type).toHaveAttribute('title', publisher);
      const link = screen.getByRole('link', {
        name: `${title} — nouvel onglet`,
      });
      expect(link).toHaveAttribute('href', url);
      expect(link).toHaveTextContent(title);
      expect(link).toContainElement(type);
    },
  );
});
