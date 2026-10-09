"""Feedback mechanism for Human-in-the-Loop (HITL) and confidence adjustment.

DA-61 Fase 6: HITL (Human-in-the-Loop) feedback mechanism and risk assessment.

Human reviewers can store feedback on diagnostics, which is used to:
- Adjust confidence scores for similar future incidents
- Track which diagnostic paths need human intervention
- Build a dataset of validated corrections

The feedback is stored in the database with TTL (time-to-live) for
temporary reviews and long-term learning.
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Literal, NotRequired, TypedDict

from app.config import settings
from app.services import get_sync_session_factory

logger = logging.getLogger(__name__)

FeedbackType = Literal["corrective", "confirmatory", "补充", "escalation"]

FeedbackStatus = Literal["pending", "approved", "rejected"]


class HumanFeedback(TypedDict):
    """Schema for human feedback on a diagnostic."""

    incident_id: str
    reviewer_id: str  # DB user ID or external system ID
    feedback_type: FeedbackType
    comments: str
    confidence_adjustment: float  # -1.0 to +1.0 (raw change to apply)
    corrected_categories: list[str]  # For corrective feedback
    status: FeedbackStatus
    created_at: str
    corrected_at: NotRequired[str]


# TTL for non-persistent feedback (7 days)
FEEDBACK_TTL_DAYS = 7


def store_feedback(
    *,
    incident_id: str,
    reviewer_id: str,
    feedback_type: FeedbackType,
    comments: str,
    confidence_adjustment: float = 0.0,
    corrected_categories: list[str] | None = None,
    status: FeedbackStatus = "pending",
) -> HumanFeedback:
    """Store human feedback for a diagnostic incident.

    Args:
        incident_id: ID of the incident being reviewed
        reviewer_id: ID of the human reviewer (from DB or external auth)
        feedback_type: Type of feedback (corrective/confirmatory/补充/escalation)
        comments: Free-text review comments
        confidence_adjustment: Numeric adjustment to apply to confidence (0.0 = no change)
        corrected_categories: List of corrected error categories (for corrective feedback)
        status: Current status of the feedback (default: "pending")

    Returns:
        The stored feedback record

    Raises:
        ValueError: If confidence_adjustment is outside [-1.0, 1.0]
    """
    if not -1.0 <= confidence_adjustment <= 1.0:
        raise ValueError("confidence_adjustment must be between -1.0 and 1.0")

    correct_categories = corrected_categories or []

    feedback = HumanFeedback(
        incident_id=incident_id,
        reviewer_id=reviewer_id,
        feedback_type=feedback_type,
        comments=comments,
        confidence_adjustment=confidence_adjustment,
        corrected_categories=correct_categories,
        status=status,
        created_at=datetime.now(tz=UTC).isoformat().replace("+00:00", "Z"),
    )

    if settings.database_url:
        try:
            session_factory = get_sync_session_factory()
            with session_factory() as session:
                session.execute(
                    """
                    INSERT INTO feedback (incident_id, reviewer_id, feedback_type,
                        comments, confidence_adjustment, corrected_categories, status, created_at)
                    VALUES (:incident_id, :reviewer_id, :feedback_type,
                        :comments, :confidence_adjustment, :corrected_categories, :status, :created_at)
                    """,
                    {
                        "incident_id": incident_id,
                        "reviewer_id": reviewer_id,
                        "feedback_type": feedback_type,
                        "comments": comments,
                        "confidence_adjustment": confidence_adjustment,
                        "corrected_categories": correct_categories,
                        "status": status,
                        "created_at": datetime.now(tz=UTC).isoformat().replace("+00:00", "Z"),
                    },
                )
                session.commit()
                logger.info("Feedback stored in DB for incident %s", incident_id)
        except Exception as e:
            logger.error("Failed to store feedback in DB: %s", e)
            raise

    return feedback


def get_feedback_for_incident(incident_id: str) -> list[HumanFeedback]:
    """Retrieve all feedback for a specific incident.

    Args:
        incident_id: ID of the incident

    Returns:
        List of feedback records (empty if none found)
    """
    feedbacks: list[HumanFeedback] = []

    if not settings.database_url:
        logger.warning("DATABASE_URL not configured, cannot query feedback")
        return feedbacks

    try:
        session_factory = get_sync_session_factory()
        with session_factory() as session:
            results = session.execute(
                """
                SELECT incident_id, reviewer_id, feedback_type, comments,
                    confidence_adjustment, corrected_categories, status, created_at
                FROM feedback WHERE incident_id = :incident_id
                ORDER BY created_at DESC
                """,
                {"incident_id": incident_id},
            )

            for row in results:
                feedbacks.append(
                    HumanFeedback(
                        incident_id=row[0],
                        reviewer_id=row[1],
                        feedback_type=row[2],
                        comments=row[3],
                        confidence_adjustment=row[4],
                        corrected_categories=row[5] or [],
                        status=row[6],
                        created_at=row[7],
                    )
                )
    except Exception as e:  # noqa: BLE001
        logger.error("Failed to retrieve feedback for incident %s: %s", incident_id, e)

    return feedbacks


def get_active_feedback(incident_id: str) -> HumanFeedback | None:
    """Get the most recent non-pending feedback for an incident.

    Args:
        incident_id: ID of the incident

    Returns:
        Most recent approved/rejected feedback, or None if not found
    """
    feedbacks = get_feedback_for_incident(incident_id)

    for fb in feedbacks:
        if fb["status"] in ("approved", "rejected"):
            return fb

    return None


def adjust_confidence_based_on_feedback(
    base_confidence: float,
    incident_id: str,
) -> tuple[float, list[str]]:
    """Adjust confidence score based on historical human feedback.

    Args:
        base_confidence: Original confidence score from LLM/diagnosis
        incident_id: ID of the incident to check feedback for

    Returns:
        Tuple of (adjusted_confidence, list of feedback comments)
    """
    feedbacks = get_feedback_for_incident(incident_id)

    if not feedbacks:
        return base_confidence, []

    total_adjustment = 0.0
    comments = []

    for fb in feedbacks:
        if fb["status"] == "approved" and fb["confidence_adjustment"] != 0.0:
            total_adjustment += fb["confidence_adjustment"]
            if fb["comments"]:
                comments.append(fb["comments"])

    adjusted = base_confidence + total_adjustment
    adjusted = max(0.0, min(1.0, adjusted))

    logger.info(
        "Confidence adjusted from %.2f to %.2f for incident %s",
        base_confidence,
        adjusted,
        incident_id,
    )

    return adjusted, comments


def get_corrected_categories(incident_id: str) -> list[str]:
    """Get corrected categories from human feedback for an incident.

    Args:
        incident_id: ID of the incident

    Returns:
        List of corrected error categories, or empty list if none found
    """
    feedbacks = get_feedback_for_incident(incident_id)

    for fb in feedbacks:
        if fb["status"] == "approved" and fb["corrected_categories"]:
            return fb["corrected_categories"]

    return []


def cleanup_expired_feedback() -> int:
    """Remove feedback records older than TTL.

    Returns:
        Number of feedback records removed
    """
    if not settings.database_url:
        logger.warning("DATABASE_URL not configured, cannot cleanup feedback")
        return 0

    cutoff = (
        (datetime.now(tz=UTC) - timedelta(days=FEEDBACK_TTL_DAYS))
        .isoformat()
        .replace("+00:00", "Z")
    )

    try:
        session_factory = get_sync_session_factory()
        with session_factory() as session:
            result = session.execute(
                """
                DELETE FROM feedback WHERE created_at < :cutoff
                """,
                {"cutoff": cutoff},
            )
            session.commit()
            logger.info("Cleaned up feedback records older than %d days", FEEDBACK_TTL_DAYS)
            return result.rowcount
    except Exception as e:  # noqa: BLE001
        logger.error("Failed to cleanup expired feedback: %s", e)
        return 0
