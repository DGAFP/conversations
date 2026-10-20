import { withoutAssistantRHSources } from '../withoutAssistantRHSources';

describe('RH API source footer presentation', () => {
  it('removes the reserved footer and preserves the answer citations', () => {
    const answer = 'Voir l’article 45.\n\nSource : guide RH, section I.';
    expect(
      withoutAssistantRHSources(`${answer}\n\n---\n**Sources :**\n1. Guide`),
    ).toBe(answer);
  });

  it('does not remove ordinary headings or public links in the answer', () => {
    const answer =
      '## Sources\n[Article](https://www.legifrance.gouv.fr/codes/article_lc/1)\n\nSuite de la réponse.';
    expect(withoutAssistantRHSources(answer)).toBe(answer);
  });

  it('handles a footer-only answer', () => {
    expect(withoutAssistantRHSources('---\n**Sources :**\n1. Guide')).toBe('');
  });
});
