# Player context — prospective research

The hourly lab collects event summaries from the existing ESPN public results
source. This is an unversioned public feed, not a guaranteed injury or lineup API.
Exact event ID, home/away names, start time, final/pre-game status and capture
clocks are checked. Never follow URLs supplied in a payload. No new subscription,
credentials or paid odds requests are used. Maximum 24 source requests per run (including up to two football catalogue lookups),
four parallel requests, with caching and retries no sooner than 45 minutes.

Availability is explicitly OUT or UNCERTAIN as reported by the source. A missing
list is unknown; an empty team list means no absences were reported. Neither is
proof that every player is healthy. No diagnosis, news text or inferred medical
condition is stored. A lineup requires exactly eleven explicitly flagged football
starters or five basketball starters; merely listing a squad is insufficient.

Completed boxscores supply stable team/player IDs, played minutes and goals or
points. NBA and NHL formats were checked on live completed and upcoming events.
Football rosters are handled when the source provides usable minute/scoring
statistics. If exact minutes are absent in normal football full-time records,
regulation minutes are derived from explicit starter/substitution/card clocks,
excluding added time, and labelled as derived. Ambiguous records stay missing.
NFL/MLB and unavailable formats remain explicit coverage gaps. No
sport's player metrics are substituted for those of another sport.

Features are frozen before kickoff and use only earlier completed UTC match
dates. They measure prior minutes and scoring shares missing/uncertain, explicit counts of reported
players with unknown history, scoring per minute in the last five appearances
versus up to twenty earlier observed team games, lineup experience and the time
since the latest observed player boxscore (a rest proxy when coverage is incomplete). Recorded wins
with/without a player's recorded participation are descriptive associations only.
At least three
earlier player boxscores per team are required. The baseline accounts for the
opponent and venue through its original probability. These are associations, not
proof of a player's causal influence on winning.

Historical boxscores do not retroactively provide pre-match injury reports or
lineups. No past context forecast is invented. The append-only context journal
freezes actual capture time, context, features, base/market probabilities, odds,
candidate weights and eventual official outcome. Duplicated wallets cannot
multiply an observation. Lineup and injury models have separate feature spaces;
sports, markets, base families and code versions have separate streams.

A fixed regularized multinomial offset model learns probability adjustments
after 40 resolved pre-match observations across at least four dates. There is no
manual probability bonus for an absent player. Every candidate is evaluated on
subsequent games, against both the original model and verified market probabilities.
Daily PAPER selection influence requires at least 100 candidate events over 14
dates and paired improvement in Brier and log loss against both benchmarks.
Until then the daily market-favourite fallback remains in place. This is not
profit or real-money approval, and no account, historic forecast or balance resets.

The dashboard shows the actual source coverage, player availability, model sample,
probability availability and whether a context candidate can influence daily
research. Missing probabilities are displayed as unavailable, never zero.
