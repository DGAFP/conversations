import { Button } from '@gouvfr-lasuite/cunningham-react';
import { UIMessage } from 'ai';
import { useState } from 'react';

import { backendUrl } from '@/api';
import { Box, StyledLink, Text } from '@/components';

interface DocumentSource {
  title: string;
  publisher: string;
  url: string;
  access: 'public' | 'authenticated';
}

const documentLinkCss = `
  color: var(--c--contextuals--content--semantic--brand--primary);
  text-decoration: underline;
`;

export function assistantRHSources(message?: UIMessage): DocumentSource[] {
  const metadata = message?.metadata as
    { assistant_rh?: { sources?: DocumentSource[] } } | undefined;
  const sources = metadata?.assistant_rh?.sources;
  return Array.isArray(sources)
    ? sources.filter(
        (source) =>
          source &&
          typeof source.title === 'string' &&
          typeof source.publisher === 'string' &&
          typeof source.url === 'string' &&
          (source.access === 'public' || source.access === 'authenticated'),
      )
    : [];
}

function documentUrl(source: DocumentSource): string | null {
  if (
    source.access === 'authenticated' &&
    /^\/api\/v[\d.]+\/chats\/[\w-]+\/messages\/[\w-]+\/sources\/\d+\/$/.test(
      source.url,
    )
  ) {
    return backendUrl() + source.url;
  }
  if (source.access === 'public') {
    try {
      const url = new URL(source.url);
      if (
        url.protocol === 'https:' &&
        [
          'www.service-public.fr',
          'www.service-public.gouv.fr',
          'www.legifrance.gouv.fr',
          'legifrance.gouv.fr',
        ].includes(url.hostname) &&
        !url.username &&
        !url.password &&
        (!url.port || url.port === '443') &&
        !url.search &&
        !url.hash
      ) {
        return source.url;
      }
    } catch {
      return null;
    }
  }
  return null;
}

export function AssistantRHSources({ message }: { message?: UIMessage }) {
  const sources = assistantRHSources(message);
  const [preview, setPreview] = useState<DocumentSource | null>(null);
  const [attempt, setAttempt] = useState(0);
  const previewUrl = preview && documentUrl(preview);

  return (
    <Box $padding={{ all: 'sm' }} $gap="12px">
      <Text as="h3">Sources sélectionnées</Text>
      {sources.length === 0 && (
        <Text role="status">
          Références documentaires indisponibles pour cette réponse. Les
          anciennes réponses sans association fiable ne permettent pas d’ouvrir
          un document.
        </Text>
      )}
      {sources.map((source, index) => {
        const url = documentUrl(source);
        return (
          <Box key={index} $gap="6px">
            <Text as="h4">{source.title}</Text>
            {source.publisher && <Text>{source.publisher}</Text>}
            {!url ? (
              <Text>Document indisponible.</Text>
            ) : (
              <>
                {source.access === 'authenticated' && (
                  <Button
                    variant="secondary"
                    onClick={() => {
                      setAttempt(0);
                      setPreview(source);
                    }}
                  >
                    Consulter le document
                  </Button>
                )}
                <StyledLink
                  $css={documentLinkCss}
                  href={url}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  {source.access === 'public'
                    ? 'Consulter la source publique'
                    : 'Ouvrir dans un nouvel onglet'}
                </StyledLink>
              </>
            )}
          </Box>
        );
      })}
      {preview && previewUrl && (
        <Box $gap="8px">
          <Text as="h4">{preview.title}</Text>
          <Text role="status">
            Si l’aperçu ne s’affiche pas, ouvrez le PDF dans un nouvel onglet ou
            téléchargez-le.
          </Text>
          <StyledLink
            $css={documentLinkCss}
            href={`${previewUrl}?download=1`}
            target="_blank"
            rel="noopener noreferrer"
          >
            Télécharger le PDF
          </StyledLink>
          <Button
            variant="tertiary"
            onClick={() => {
              setAttempt((value) => value + 1);
            }}
          >
            Réessayer l’aperçu
          </Button>
          <Button variant="tertiary" onClick={() => setPreview(null)}>
            Fermer l’aperçu
          </Button>
          <iframe
            key={`${previewUrl}-${attempt}`}
            title={`Aperçu PDF — ${preview.title}`}
            src={`${previewUrl}?attempt=${attempt}`}
            style={{ width: '100%', minHeight: '65vh', border: 0 }}
          />
        </Box>
      )}
    </Box>
  );
}
