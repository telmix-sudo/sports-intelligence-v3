"""Shared daily PAPER activity policy; targets never fabricate valid opportunities."""
import math
from decimal import Decimal, ROUND_DOWN

MIN_DAILY_ENTRIES = 5
MAX_DAILY_ENTRIES = 10
NEW_STAKE_FRACTION = .0025
MAX_ACCOUNT_EXPOSURE = .02
MAX_MARKETS_PER_EVENT_ACCOUNT = 2
MINIMUM_ACTIVITY_STAKE_FRACTION = .001
MINIMUM_STAKE_EUR = 1.0


def daily_entry_stake(bank, exposure, opened_today):
    """Spread the existing PAPER budget across the first five qualified entries.

    Smaller initial stakes leave room for other wallets and later opportunities.
    Admission rules, the 2% account ceiling and aggregate risk stay unchanged.
    """
    if not all(math.isfinite(x) for x in (bank, exposure)) or bank <= 0 or exposure < 0:
        return 0.0
    preferred = bank * NEW_STAKE_FRACTION
    if opened_today < MIN_DAILY_ENTRIES:
        preferred = min(preferred, max(MINIMUM_STAKE_EUR, bank * MINIMUM_ACTIVITY_STAKE_FRACTION))
    available = max(0.0, bank * MAX_ACCOUNT_EXPOSURE - exposure)
    size = float(Decimal(str(min(preferred, available))).quantize(Decimal('.01'), rounding=ROUND_DOWN))
    return size if size >= MINIMUM_STAKE_EUR else 0.0
