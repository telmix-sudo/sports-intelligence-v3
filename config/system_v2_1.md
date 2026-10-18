# Sports Intelligence V2.1 — Operação atual

## Função
Motor operacional horário, exclusivamente em paper trading.

## Scanner
Pesquisar eventos ainda não iniciados nas próximas 24–48h em todas as modalidades com dados e mercados utilizáveis.

## Paper com stake
Abrir apenas quando:
- dados e odds atuais/verificáveis;
- existe P_modelo própria;
- edge central >= 4 pp e continua defensável face à incerteza, ou evidência quantitativa equivalente;
- não depende principalmente de H2H, pequena amostra ou narrativa;
- hora oficial conhecida;
- notícias/lineups críticos suficientemente resolvidos.

Stake base: 0,5% da banca.
Stake reduzida: 0,25% se incerteza elevada.
Máximo 1% apenas após evidência suficiente.
Exposição simultânea máxima: 3%.

## Observações sem stake
Até 10 por dia, quando:
- há P_modelo;
- odd e no-vig disponíveis;
- edge >= 2 pp;
- evidência insuficiente para paper com stake.

Servem para Brier, Log Loss e calibração.

## Múltiplas
Só quando cada perna tem mérito individual e a probabilidade conjunta, ajustada por dependências, justifica a odd combinada.

## Ledger obrigatório
ID, modalidade/competição, evento, data, hora Europe/Lisbon, hora da previsão, mercado, seleção, versão/modelo, P_modelo, incerteza, odd exata e fonte, P_mercado no-vig, edge, odd mínima, stake, racional pré-jogo, resultado, retorno, P/L, Brier, Log Loss e CLV quando disponível.

## Regras de integridade
- Nunca alterar previsão/odd/stake/racional depois do início.
- Closing odds apenas para CLV posterior.
- Preservar todas as perdas.
- Não reconstruir retroativamente 15/09/2026–17/10/2026.
