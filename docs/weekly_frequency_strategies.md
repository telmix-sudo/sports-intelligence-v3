# V3 — três carteiras PAPER por frequência semanal

Criadas em 27/10/2026. São carteiras independentes, cada uma com banca fictícia inicial de 1.000 EUR e ledger próprio. Não alteram os saldos nem os registos das nove carteiras anteriores.

| Estratégia | Objetivo por semana | Edge mínimo | Buffer conservador | Exposição máxima |
| --- | --- | --- | --- | --- |
| FREQ_SELECTIVE_1_3 | 1–3 | 6% | 8 pp | 1% |
| FREQ_BALANCED_3_5 | 3–5 | 4,5% | 6,5 pp | 1,5% |
| FREQ_BROAD_5_10 | 5–10 | 3% | 5 pp | 2% |

Todas usam a previsão própria Elo–Poisson 1X2, no mínimo 16 jogos históricos por equipa, odds completas pré-jogo de The Odds API, pelo menos duas casas e atualização das cotações até seis horas. Cada posição fictícia tem stake de 0,25% da banca, limitada pela exposição. Os buffers são hipóteses de investigação e **não intervalos de confiança calibrados**. O EV conservador deve ser positivo, além do edge mínimo.

As metas são objetivos e tetos, nunca quotas: zero entradas é aceitável quando não existem candidatos elegíveis. Apenas futebol 1X2 está suportado por este módulo; alargar a outros mercados requer modelos, odds e regras de liquidação próprios. O sistema guarda as previsões nos ledgers individuais e usa o mecanismo existente de liquidação de resultados oficiais. O relatório do laboratório apresenta os diagnósticos de frequência. A data de criação é registada na primeira execução após a implementação.

Limitações: a qualidade das odds e o feed podem restringir o número de oportunidades; o modelo não está validado fora da amostra. Não existe execução com dinheiro real nem garantia de frequência ou lucro. As carteiras podem selecionar os mesmos eventos e os resultados são correlacionados. A estratégia MARKET_DAILY_REFERENCE é um controlo separado, não sinal de valor.
