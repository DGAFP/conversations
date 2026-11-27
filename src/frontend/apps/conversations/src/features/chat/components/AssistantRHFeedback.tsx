import { Button, Checkbox, TextArea } from '@gouvfr-lasuite/cunningham-react';
import { FormEvent, useId, useRef, useState } from 'react';

import { Icon } from '@/components';
import {
  AssistantRHFeedback as FeedbackValue,
  useAssistantRHFeedback,
} from '@/features/chat/api/useAssistantRHFeedback';

import './AssistantRHFeedback.css';

const POSITIVE_REASONS = ['Clair', 'Utile', 'Pertinent', 'Complet', 'Précis'];
const NEGATIVE_REASONS = [
  'Confus',
  'Éléments faux',
  'Non pertinent',
  'Incomplet',
  'Sources manquantes',
];
const RATING_LABELS = [
  'Très insatisfaisant',
  'Insatisfaisant',
  'Moyennement satisfaisant',
  'Satisfaisant',
  'Très satisfaisant',
];

interface Props {
  conversationId: string;
  messageId: string;
}

export function AssistantRHFeedback({ conversationId, messageId }: Props) {
  const id = useId();
  const starsRef = useRef<HTMLInputElement>(null);
  const { query, mutation } = useAssistantRHFeedback(conversationId, messageId);
  const [draft, setDraft] = useState<FeedbackValue | null>(null);
  const [expanded, setExpanded] = useState(false);
  const [hoveredStars, setHoveredStars] = useState<number | null>(null);
  const [validationError, setValidationError] = useState(false);
  const feedback = draft ?? query.data;
  const stars = feedback?.stars ?? 0;
  const showPositive = stars >= 3;
  const showNegative = stars > 0 && stars <= 4;
  const disabled = query.isPending || query.isError || mutation.isPending;

  function selectStars(value: number) {
    setDraft({
      stars: value,
      reasons_positive: value >= 3 ? (feedback?.reasons_positive ?? []) : [],
      reasons_negative: value <= 4 ? (feedback?.reasons_negative ?? []) : [],
      comment: feedback?.comment ?? '',
    });
    setExpanded(true);
    setValidationError(false);
    mutation.reset();
  }

  function toggleReason(
    key: 'reasons_positive' | 'reasons_negative',
    reason: string,
  ) {
    if (!feedback) return;
    const reasons = feedback[key];
    setDraft({
      ...feedback,
      [key]: reasons.includes(reason)
        ? reasons.filter((item) => item !== reason)
        : [...reasons, reason],
    });
    setValidationError(false);
  }

  function closePanel() {
    setExpanded(false);
    setDraft(null);
    setValidationError(false);
    mutation.reset();
    starsRef.current?.focus();
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!feedback || mutation.isPending) return;
    const value = { ...feedback, comment: feedback.comment.trim() };
    if (
      !value.comment &&
      !value.reasons_positive.length &&
      !value.reasons_negative.length
    ) {
      setValidationError(true);
      return;
    }
    try {
      await mutation.mutateAsync(value);
      closePanel();
    } catch {
      // Keep the draft and show the mutation error inline so it can be retried.
    }
  }

  return (
    <section className="rh-feedback" aria-label="Évaluation de la réponse">
      <div className="rh-feedback__rating-row">
        <fieldset className="rh-feedback__rating" disabled={disabled}>
          <legend>Comment évaluez-vous la réponse ?</legend>
          <div
            className="rh-feedback__stars"
            onMouseLeave={() => setHoveredStars(null)}
          >
            {[1, 2, 3, 4, 5].map((value) => (
              <span
                key={value}
                className="rh-feedback__star"
                data-filled={value <= (hoveredStars ?? stars)}
                onMouseEnter={() => setHoveredStars(value)}
              >
                <input
                  id={`${id}-star-${value}`}
                  ref={value === (stars || 1) ? starsRef : undefined}
                  type="radio"
                  name={`${id}-stars`}
                  value={value}
                  checked={stars === value}
                  onChange={() => selectStars(value)}
                  onClick={() => {
                    if (stars === value && !expanded) selectStars(value);
                  }}
                  aria-label={`${value} ${value === 1 ? 'étoile' : 'étoiles'} : ${RATING_LABELS[value - 1]}`}
                  aria-controls={expanded ? `${id}-panel` : undefined}
                />
                <svg viewBox="0 0 24 24" aria-hidden="true">
                  <path d="m12 3 2.78 5.63L21 9.54l-4.5 4.39 1.06 6.2L12 17.2l-5.56 2.93 1.06-6.2L3 9.54l6.22-.91L12 3Z" />
                </svg>
              </span>
            ))}
            {stars > 0 && <span className="rh-feedback__score">{stars}/5</span>}
          </div>
        </fieldset>
        {query.data && !expanded && (
          <div className="rh-feedback__saved" role="status">
            <span>
              <Icon iconName="check_circle" /> Merci pour votre retour
            </span>
            <Button
              size="small"
              variant="tertiary"
              onClick={() => selectStars(stars)}
            >
              Modifier mon évaluation
            </Button>
          </div>
        )}
      </div>
      {query.isError && (
        <div className="rh-feedback__error" role="alert">
          Impossible de charger votre évaluation.
          <Button
            size="small"
            variant="tertiary"
            onClick={() => void query.refetch()}
          >
            Réessayer
          </Button>
        </div>
      )}
      {query.isPending && (
        <span role="status" className="sr-only">
          Chargement de votre évaluation…
        </span>
      )}
      {expanded && feedback && (
        <form
          id={`${id}-panel`}
          className="rh-feedback__panel"
          aria-labelledby={`${id}-title`}
          onSubmit={(event) => void submit(event)}
        >
          <div className="rh-feedback__heading">
            <div>
              <h3 id={`${id}-title`}>Aidez-nous à améliorer cette réponse</h3>
              <p>Votre retour contribue à améliorer l’Assistant RH.</p>
            </div>
            <Button
              size="small"
              color="neutral"
              variant="tertiary"
              type="button"
              disabled={mutation.isPending}
              onClick={closePanel}
              aria-label="Fermer le formulaire d’évaluation"
              icon={<Icon iconName="close" />}
            />
          </div>
          {showPositive && (
            <fieldset
              className="rh-feedback__reasons"
              disabled={mutation.isPending}
            >
              <legend>Qu’avez-vous particulièrement apprécié ?</legend>
              <div className="rh-feedback__choices">
                {POSITIVE_REASONS.map((reason) => (
                  <Checkbox
                    key={reason}
                    label={reason}
                    checked={feedback.reasons_positive.includes(reason)}
                    onChange={() => toggleReason('reasons_positive', reason)}
                  />
                ))}
              </div>
            </fieldset>
          )}
          {showNegative && (
            <fieldset
              className="rh-feedback__reasons"
              disabled={mutation.isPending}
            >
              <legend>Qu’est-ce qui pourrait être amélioré ?</legend>
              <div className="rh-feedback__choices">
                {NEGATIVE_REASONS.map((reason) => (
                  <Checkbox
                    key={reason}
                    label={reason}
                    checked={feedback.reasons_negative.includes(reason)}
                    onChange={() => toggleReason('reasons_negative', reason)}
                  />
                ))}
              </div>
            </fieldset>
          )}
          <TextArea
            variant="classic"
            label="Commentaire (facultatif)"
            placeholder="Précisez ce qui vous a aidé ou ce qui pourrait être amélioré…"
            value={feedback.comment}
            onChange={(event) => {
              setDraft({ ...feedback, comment: event.target.value });
              setValidationError(false);
            }}
            rows={3}
            maxLength={4000}
            disabled={mutation.isPending}
            aria-describedby={`${id}-hint`}
          />
          <p id={`${id}-hint`} className="rh-feedback__hint">
            Sélectionnez au moins un motif ou ajoutez un commentaire.
          </p>
          {validationError && (
            <p className="rh-feedback__error" role="alert">
              Pour envoyer votre évaluation, sélectionnez un motif ou écrivez un
              commentaire.
            </p>
          )}
          {mutation.isError && (
            <p className="rh-feedback__error" role="alert">
              Votre évaluation n’a pas pu être enregistrée. Votre saisie est
              conservée, vous pouvez réessayer.
            </p>
          )}
          <div className="rh-feedback__actions">
            <Button
              type="button"
              size="small"
              color="neutral"
              variant="tertiary"
              disabled={mutation.isPending}
              onClick={closePanel}
            >
              Annuler
            </Button>
            <Button type="submit" size="small" disabled={mutation.isPending}>
              {mutation.isPending
                ? 'Envoi en cours…'
                : 'Envoyer mon évaluation'}
            </Button>
          </div>
        </form>
      )}
    </section>
  );
}
