# Historical probability learning (PAPER only)

The hourly lab replays completed score/statistic history chronologically. Every
match on a date is predicted before any result from that date updates its model.
The learner also reads genuinely frozen, settled forecasts, excluding settlements
recorded today, voids and forecasts recorded after kickoff. It never changes them.
Shared auxiliary forecasts and frequency-model events count once per stream.
Streams retain sport, model family and exact market/line identity.

Each stream needs 300 examples and 40 dates. Four disjoint date blocks use 55%,
20%, 10% and 15% of dates: training estimates bounded class corrections, tuning
selects correction strength, calibration selects temperature, and final evaluation
compares the frozen candidate with its unmodified model. Neither final labels nor
their scores select parameters. Brier and Log Loss must both improve. This is
exploratory score evaluation; repeated daily reuse is not fresh independent proof.

The resulting candidate initially runs in shadow. New forecasts store both raw
and candidate probabilities, model hash, cutoff and status. Its exact pre-event
probabilities are then scored against official settled outcomes. At least 100
distinct events across 14 dates must improve both metrics against BOTH the
original model and the verified contemporaneous market, with the paired mean
loss difference plus 1.96 standard errors below zero, before future PAPER entries
can use that stream's adjustments. This operational safeguard is not proof of
profit or a multiplicity-adjusted statistical claim. Subsequent failures revert
to the original probabilities. Existing price, admission, exposure and size gates
remain required. No historical bankroll, stake or price is fabricated.

All thirteen wallets participate through their original or shared auxiliary
models. Frequency wallets share their blend model's evidence. MARKET_ANCHORED
requires its own timed odds history; scores alone cannot reconstruct it. The
daily market favourite remains the unchanged control, with learning in shadow;
it receives no new shared auxiliary model positions. Markets with
insufficient history remain in collection. Football history does not train NBA,
NHL, NFL, MLB or corner/offside models.

The strategy report and dashboard publish actual per-stream/per-wallet states.
Historical ROI remains unavailable without contemporaneous verified odds.

## Admission and shared exposure

All new model-based PAPER positions also require prospective evidence against
the verified market, independently of score-only backtests. Original models need
100 distinct events across 14 dates, a negative distribution-free paired Brier
upper bound and lower Log Loss than the market. Research models additionally
retain their existing score, calibration and market-validation gates. Learned
adjustments require the stronger original-and-market checks above. Cached
auxiliary forecasts are rechecked against current admission evidence.

While a model is unqualified, its timestamped forecasts continue with zero stake.
Original/frequency predictions use `data/decision_observations_v3.jsonl`;
experimental markets keep their existing research journal. Both journals append
official settlements and feed the next day's validation and learning. Same-day
settlements never change today's admission. Frequency/shared copies do not
inflate event counts. No observation creates or resets a wallet balance.

One identical shared signal (event, exact market, model family and selection)
has at most EUR2.50 of NEW aggregate open PAPER exposure, the standard 0.25%
budget of one initial EUR1000 wallet. Copies across wallets share that budget.
The existing EUR36 event and EUR240 overall limits remain. Legacy positions may
already exceed the new signal budget; they are preserved, and additions blocked.
The 5–10 daily target never overrides any validation or risk limit.
