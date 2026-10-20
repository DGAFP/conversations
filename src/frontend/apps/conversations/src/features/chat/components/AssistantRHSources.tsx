import { UIMessage } from 'ai';

import { backendUrl } from '@/api';
import { Box, Icon, StyledLink, Text } from '@/components';

interface DocumentSource {
  title: string;
  publisher: string;
  url: string;
  access: 'public' | 'authenticated';
}

const sourceRowCss = `
  display: block;
  min-width: 0;
  width: 100%;
  border-radius: 4px;
  padding: var(--c--globals--spacings--xs);
  font-size: 14px;
`;

const documentLinkCss = `
  ${sourceRowCss}
  color: var(--c--contextuals--content--semantic--neutral--primary);
  text-decoration: none;
  transition: background-color 0.3s;
  &:hover {
    background-color: var(--c--contextuals--background--semantic--overlay--primary);
  }
  &:focus-visible {
    outline: 2px solid var(--c--contextuals--content--semantic--brand--primary);
    outline-offset: 2px;
  }
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

  return (
    <Box $padding={{ all: 'xs' }} $gap="4px" $css="min-width: 0; width: 100%;">
      {sources.length === 0 && (
        <Text role="status" $padding={{ all: 'xs' }}>
          Références documentaires indisponibles pour cette réponse. Les
          anciennes réponses sans association fiable ne permettent pas d’ouvrir
          un document.
        </Text>
      )}
      {sources.map((source, index) => {
        const url = documentUrl(source);
        const content = (
          <>
            <Box
              $direction="row"
              $align="center"
              $gap="4px"
              $margin={{ bottom: 'xxxs' }}
              $css="color: var(--c--contextuals--content--semantic--neutral--secondary);"
            >
              <Text $css="flex-shrink: 0; white-space: nowrap;">
                {index + 1} ·
              </Text>
              <Icon
                iconName={
                  source.access === 'public' ? 'language' : 'description'
                }
                $theme="neutral"
                $variation="secondary"
                $size="md"
                $css="flex-shrink: 0;"
                aria-hidden
              />
              <Text
                title={source.publisher}
                $css="display: block; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;"
              >
                {source.publisher ||
                  (source.access === 'public' ? 'Article' : 'Document')}
              </Text>
            </Box>
            <Text $css="color: var(--c--contextuals--content--semantic--neutral--primary); font-weight: 500; overflow-wrap: anywhere;">
              {source.title}
            </Text>
          </>
        );

        return url ? (
          <StyledLink
            key={index}
            $css={documentLinkCss}
            href={url}
            target="_blank"
            rel="noopener noreferrer"
            aria-label={`${source.title} — nouvel onglet`}
            title="Ouvrir dans un nouvel onglet"
          >
            {content}
          </StyledLink>
        ) : (
          <Box key={index} $css={sourceRowCss}>
            {content}
            <Text>Document indisponible.</Text>
          </Box>
        );
      })}
    </Box>
  );
}
