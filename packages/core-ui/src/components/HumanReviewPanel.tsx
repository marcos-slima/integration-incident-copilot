import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { reviewIncident, type HumanReviewAction } from '../api/diagnosis';
import { ApiError, describeApiError } from '../api/http';
import type { DiagnosisResponse } from '../api/contracts';

export function HumanReviewPanel({
  result,
  onReviewed,
}: {
  result: DiagnosisResponse;
  onReviewed: (action: HumanReviewAction, edit?: string) => void;
}) {
  const { t } = useTranslation();
  const state = result.human_review;
  const [edit, setEdit] = useState(result.probable_root_cause);
  const [busy, setBusy] = useState(false);
  const [reviewed, setReviewed] = useState(false);
  const [error, setError] = useState('');
  if (!state || state.status !== 'pending') return null;
  const pending = state;

  async function submit(action: HumanReviewAction) {
    if (!pending.incident_id || !pending.actions.includes(action) || busy) return;
    if (action === 'edit' && !edit.trim()) return;
    setBusy(true);
    setError('');
    try {
      await reviewIncident(pending.incident_id, action, edit.trim());
      setReviewed(true);
      onReviewed(action, action === 'edit' ? edit.trim() : undefined);
    } catch (cause) {
      setError(cause instanceof ApiError ? describeApiError(cause) : t('core.common.networkError'));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="human-review" aria-live="polite">
      <div className="section-label">{t('core.review.title')}</div>
      {!pending.can_resume ? (
        <p className="review-hint">{t('core.review.persistenceRequired')}</p>
      ) : reviewed ? (
        <p className="review-hint">{t('core.review.completed')}</p>
      ) : (
        <>
          <p className="review-hint">{t('core.review.pending')}</p>
          <textarea
            className="field-input"
            value={edit}
            onChange={(event) => setEdit(event.target.value)}
            aria-label={t('core.review.editLabel')}
            disabled={busy}
          />
          <div className="btn-row">
            <button className="submit-btn" onClick={() => submit('approve')} disabled={busy}>
              {t('core.review.approve')}
            </button>
            <button className="advanced-toggle" onClick={() => submit('edit')} disabled={busy}>
              {t('core.review.edit')}
            </button>
            <button className="advanced-toggle" onClick={() => submit('reject')} disabled={busy}>
              {t('core.review.reject')}
            </button>
          </div>
        </>
      )}
      {error && <div className="form-error">{error}</div>}
    </section>
  );
}
