# V3 review and implementation

Follow the repository AGENTS.md. Review recommendations should identify a concrete
failure, affected event/market and a useful verification. Prioritize accounting,
data leakage, settlement, stale odds, shared API quotas and reproducible evaluation.

Keep optional Optuna/LightGBM work in ml_lab.py and its independent workflow.
The daily experiment never adds a funded wallet and never changes any existing
selection model. Compare Log Loss and Brier against chronological Elo/Poisson
baselines; score-only datasets cannot demonstrate profit. Preserve all portfolios.

Run the relevant tests and report their actual outcome. Clearly distinguish
configuration present, external app installed and a verified successful run.
Do not propose subscriptions or enable paid usage to make a workflow pass.
