"""Rules that decide whether the service may start.

Kept separate from the interface so they can be tested directly.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StartDecision:
    allowed: bool
    reason: str = ""


def can_start(
    *,
    is_admin: bool,
    real_internet: bool,
    test_mode: bool,
    has_enabled_domains: bool,
) -> StartDecision:
    if not has_enabled_domains:
        return StartDecision(False, "אין דומיינים פעילים. הוסף דומיין והפעל אותו לפני ההפעלה.")
    if not is_admin:
        return StartDecision(False, "יש להפעיל את התוכנה כמנהל מערכת.")
    if real_internet and not test_mode:
        return StartDecision(
            False,
            "זוהה חיבור אינטרנט אמיתי. השירות לא יופעל כדי לא לשבש את הגלישה. "
            "אם ברצונך לבדוק את השירות בזמן שיש אינטרנט, סמן 'עקיפת בדיקת אינטרנט'.",
        )
    return StartDecision(True)


def must_auto_stop(*, running: bool, real_internet: bool, test_mode: bool) -> bool:
    """While running outside test mode, a detected real connection stops the service."""
    return running and real_internet and not test_mode
