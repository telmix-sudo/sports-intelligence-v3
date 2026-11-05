# Auditing choices and testing the opposite policy

The production PAPER selector maximizes `model_probability - market_no_vig`,
subject to all existing admission, expected value, quote and risk gates. This is
not the same as choosing the outcome most likely to win. Poorly calibrated
probabilities close to 50% can make outsiders appear attractive against a much
stronger market favourite. The normal-margin sign, home/away quote order, final
outcome conversion and payout arithmetic are separate checks from model quality.

A shared auxiliary forecast copied to several wallets is a single model signal.
The read-only audit attributes native and shared positions separately, reports
price bands and selected model/market probabilities, and compares payouts with
frozen outcomes. A bounded, cached ESPN summary check independently verifies
model event IDs, home/away identities and final scores. Missing or conflicting
results are visible; the audit never repairs old balances or predictions.

## Registered future comparison

`data/selection_experiment_v3.jsonl` is a separate append-only, zero-stake journal.
Only forecasts made by the current lab before kickoff with complete, genuine
bookmaker clocks (at most eight hours old) and at least two books are eligible.
No old settled forecast is imported. The first valid forecast appends the policy
registration before any observation. Every arm sees the same game, full odds
vector and capture time, and is evaluated at one theoretical euro per choice:

- `VALUE_GAP`: maximum model probability minus margin-free market probability.
- `OPPOSITE_VALUE_GAP`: the other outcome, only for complementary binary markets.
- `MODEL_FAVOURITE`: maximum model probability.
- `MARKET_FAVOURITE`: maximum market probability; the control.

Ties use the first quoted outcome. A 1X2 draw is not mapped to an invented opposite
or an unquoted double chance. An opposite selection does not invert the original
probability vector or claim a new calibrated probability model.

One event per exact model/sport/market/source-code signature is frozen. Wallet
copies cannot add observations. Different lines, periods and model code versions
have separate cohorts. A cohort locks at the first forecast prefix reaching 100
unique events and 14 distinct UTC kickoff dates, irrespective of gains, losses
or settlement order. It then refuses further forecasts. The final assessment
waits for every cohort outcome; missing results never count as losses and pushes
are VOID for every arm.

The report shows hit rates, theoretical unit PnL/ROI and original probability
Brier/log loss against the market. Only a fully resolved cohort with at least 100
decisive results over 14 dates gets descriptive paired day-block bootstrap
intervals for the opposite policy versus both the original and market favourite.
These intervals preserve same-day dependence, but do not settle repeated-team,
multiple-stream or model-search uncertainty. Further independent confirmation
and existing admission gates are required before any practical adoption.
There is **no automatic stake activation** and no access to `PaperRiskBook` from
the experiment. Historical inverse results remain hypothesis-generation data.
The original wallets, balances, positions and frozen forecasts remain preserved.

## Explicit daily PAPER minimum

On 4 November 2026 the user separately authorized five nonzero PAPER entries per
wallet before edge validation. The fixed-cohort experiment remains zero-stake,
unchanged and never reserves funds. The observer exposes current recomputed
inputs in memory, including after deduplication or cohort closure; a separate
`daily_paper_minimum.py` allocator can freeze new labelled EUR 0.50 research
positions from those current inputs. It does not rewrite a paired observation,
use old frozen probabilities as a current forecast, or classify an unvalidated
model as market-validated. Ordinary value entries retain their original gates.
