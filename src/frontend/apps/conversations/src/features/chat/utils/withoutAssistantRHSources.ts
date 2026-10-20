// Reserved footer appended by the RH API, never an arbitrary Markdown heading.
const SOURCES_MARKER = '\n\n---\n**Sources :**\n';

/** Replace only the API-owned footer when authoritative UI sources are available. */
export function withoutAssistantRHSources(content: string): string {
  if (content.startsWith(SOURCES_MARKER.slice(2))) return '';
  const index = content.indexOf(SOURCES_MARKER);
  return index < 0 ? content : content.slice(0, index);
}
