# Daily PAPER minimum — release 5 November 2026

The user explicitly requested at least **five nonzero virtual-stake entries per
wallet per Lisbon calendar day**, including during model validation. This replaces
the previous qualified-entry-only minimum which could leave model wallets at zero.

Ordinary value entries are evaluated first and retain all their existing admission
and sizing rules. A separate daily research allocator fills only the remaining
places up to five, at **EUR 0.50 fictitious per position**. Ordinary positions still
require the normal minimum stake and validated forward edge. The research entries
are labelled `DAILY_EXPLORATORY_PAPER`, with `validated_edge: false`; their model EV
can be negative. They are experiments, not validated betting recommendations.

## Data and capital controls

- Current recomputed model forecasts, no more than 15 minutes old at registration.
- Real future event IDs/names and genuine complete price vectors from at least two
  bookmakers; the original book clocks are retained and must be at most 8 hours old.
- Actual capture time before kickoff; no historical or post-start entries.
- Five distinct new events per wallet when filling from zero; an existing event is
  not repeated in the same wallet, including across calendar days.
- Maximum 10 total daily positions per wallet, 2% account exposure, EUR 36 per
  event and EUR 240 across the original and experimental PAPER portfolios.
- The existing EUR 2.50 aggregate cap for the same model/event/market/selection
  remains shared across every wallet; profile labels do not evade that cap.
- Source model and current snapshot are recorded, and fixed goal markets have
  explicit settlement rules. Three-way markets have no invented opposite.
- No real bets, deposits, subscriptions, paid feeds, fabricated odds or bank resets.
  The existing 16-credit daily odds ceiling is unchanged.

## Guarded daily research selector

This selector applies only to daily research complements. Native validated entries
continue to take precedence. For 1X2 sources the wallet's own model is preferred;
for other sports the real shared source model is explicitly recorded.

Every model wallet uses `VALIDATED_MODEL_OR_MARKET_FAVOURITE`. A learned model's
favourite may be used only when its frozen forward calibration gate reports
`ACTIVE`, at least 100 events and 14 dates with both original and verified market
benchmarks, and the paired Brier/log-loss checks have passed. Until then daily
research uses the market favourite. Original model probabilities remain frozen
and attributable, including when the selected side differs from the model's pick.

The largest unvalidated probability gap and its binary opposite are compared
only in the zero-stake paired experiment. The initial proposed four-way split
was never deployed and is replaced by this guarded selector. A higher expected
hit rate is not evidence of positive return: bookmaker margin and losses remain
possible. No claim of validated profitability follows from daily activity.

MARKET_DAILY_REFERENCE stays a pure market-favourite control, using market
probabilities rather than auxiliary model probabilities. Its ordinary routine
continues to allow up to ten entries per day.

The allocator runs in every existing laboratory execution, is idempotent and
counts real positive virtual stakes rather than zero-stake observations. Frozen
records, previous PnL and existing account IDs are preserved. Missing quotes,
started games, absent forecasts and exhausted risk budgets are reported as explicit
blockers; a target never creates a fictitious event or quote.

The zero-stake selection-policy experiment remains a separate journal and does
not automatically promote a model. Daily research positions are a distinct,
explicitly user-authorized PAPER activity policy, with their own classification.
