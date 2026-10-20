import { fireEvent, render, screen } from '@testing-library/react';
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
  it('opens an application PDF preview and offers protected new-tab/download links', () => {
    render(<AssistantRHSources message={message([source])} />);
    expect(screen.getByText('DGAFP')).toBeInTheDocument();
    const link = screen.getByRole('link', {
      name: 'Ouvrir dans un nouvel onglet',
    });
    expect(link.getAttribute('href')).toContain(path);
    expect(link).toHaveAttribute('target', '_blank');
    fireEvent.click(
      screen.getByRole('button', { name: 'Consulter le document' }),
    );
    expect(
      screen.getByTitle('Aperçu PDF — Guide des contractuels'),
    ).toHaveAttribute('src', expect.stringContaining(path));
    expect(
      screen.getByRole('link', { name: 'Télécharger le PDF' }),
    ).toHaveAttribute('href', expect.stringContaining('?download=1'));
    expect(screen.getByRole('status')).toHaveTextContent(
      'Si l’aperçu ne s’affiche pas',
    );
    fireEvent.click(screen.getByRole('button', { name: 'Réessayer l’aperçu' }));
    expect(
      screen.getByTitle('Aperçu PDF — Guide des contractuels'),
    ).toBeInTheDocument();
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
});
