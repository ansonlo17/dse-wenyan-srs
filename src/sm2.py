"""SM-2 spaced repetition algorithm."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

# UI rating -> SM-2 quality
RATING_QUALITY = {
    0: 1,  # Again
    1: 3,  # Hard
    2: 4,  # Good
    3: 5,  # Easy
}

RATING_LABELS = {
    0: "再來一次",
    1: "困難",
    2: "尚可",
    3: "簡單",
}

# UI left → right order (Easy … Again)
RATING_BUTTON_ORDER = (3, 2, 1, 0)


@dataclass
class SRSState:
    ease_factor: float = 2.5
    interval_days: float = 0.0
    repetitions: int = 0
    lapses: int = 0
    due_at: str | None = None


def _parse_due(due_at: str | None) -> datetime:
    if not due_at:
        return datetime.now(timezone.utc)
    try:
        dt = datetime.fromisoformat(due_at.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return datetime.now(timezone.utc)


def review(state: SRSState, rating: int, now: datetime | None = None) -> SRSState:
    """
    Apply SM-2 given UI rating 0..3.
    """
    now = now or datetime.now(timezone.utc)
    q = RATING_QUALITY.get(rating, 4)
    ef = state.ease_factor or 2.5
    reps = state.repetitions or 0
    interval = float(state.interval_days or 0)
    lapses = state.lapses or 0

    # update ease
    ef = ef + (0.1 - (5 - q) * (0.08 + (5 - q) * 0.02))
    if ef < 1.3:
        ef = 1.3

    if q < 3:
        # failed
        reps = 0
        interval = 0.0 if rating == 0 else 1.0
        lapses += 1
        # Again: due in ~10 minutes for same-session retry feel; store as fraction of day
        if rating == 0:
            due = now + timedelta(minutes=10)
            interval = 10 / (24 * 60)
        else:
            due = now + timedelta(days=1)
            interval = 1.0
    else:
        reps += 1
        if reps == 1:
            interval = 1.0
        elif reps == 2:
            interval = 3.0 if rating == 1 else 6.0
        else:
            interval = max(1.0, round(interval * ef))
            if rating == 1:  # hard: slightly shorter
                interval = max(1.0, round(interval * 0.8))
            elif rating == 3:  # easy: longer
                interval = max(1.0, round(interval * 1.3))
        due = now + timedelta(days=interval)

    return SRSState(
        ease_factor=round(ef, 4),
        interval_days=float(interval),
        repetitions=reps,
        lapses=lapses,
        due_at=due.replace(microsecond=0).isoformat(),
    )


def state_from_card(card: dict[str, Any]) -> SRSState:
    return SRSState(
        ease_factor=float(card.get("ease_factor") or 2.5),
        interval_days=float(card.get("interval_days") or 0),
        repetitions=int(card.get("repetitions") or 0),
        lapses=int(card.get("lapses") or 0),
        due_at=card.get("due_at"),
    )


def apply_review_to_db(
    vocab_id: int, card: dict[str, Any], rating: int, user_id: int
) -> SRSState:
    from . import db

    new_state = review(state_from_card(card), rating)
    db.update_srs(
        vocab_id=vocab_id,
        ease_factor=new_state.ease_factor,
        interval_days=new_state.interval_days,
        repetitions=new_state.repetitions,
        due_at=new_state.due_at or db.now_iso(),
        lapses=new_state.lapses,
        rating=rating,
        user_id=user_id,
    )
    # auto-master if very stable
    if new_state.interval_days >= 60 and new_state.repetitions >= 5 and rating >= 2:
        db.update_vocab_status(vocab_id, "mastered", user_id=user_id)
    return new_state


def touch_streak(user_id: int) -> int:
    """Update study streak for this user; return current streak count."""
    from . import db

    today = datetime.now().date().isoformat()
    last = db.get_setting("streak_last_date", "", user_id=user_id)
    count = int(db.get_setting("streak_count", "0", user_id=user_id) or 0)
    if last == today:
        return count
    if last:
        try:
            last_d = datetime.fromisoformat(last).date()
            delta = (datetime.now().date() - last_d).days
            if delta == 1:
                count += 1
            elif delta > 1:
                count = 1
        except ValueError:
            count = 1
    else:
        count = 1
    db.set_setting("streak_last_date", today, user_id=user_id)
    db.set_setting("streak_count", str(count), user_id=user_id)
    return count
