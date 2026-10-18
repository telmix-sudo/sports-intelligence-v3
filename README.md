# Sports Intelligence v3

Sistema experimental de inteligência desportiva para descoberta, teste e validação de possíveis ineficiências de mercado.

## Estado
- V1 concluída: 5 papers, 4V-1D, P/L +0,1644 €, banca final 1.000,1644 €.
- V2.1 ativa em paper trading, com scanner horário.
- V3 em desenvolvimento neste repositório.
- Nenhum modelo está ainda classificado como validado.
- Capital real permanece bloqueado.

## Objetivos da V3
1. Fonte única de verdade para chat principal e automações.
2. Ledger persistente e auditável.
3. Backtesting cronológico sem data leakage.
4. Modelos simples por modalidade, evoluindo apenas com melhoria OOS demonstrada.
5. Comparação sistemática com mercado no-vig.
6. Métricas: Brier, Log Loss, calibração, ROI/yield, drawdown e CLV.
7. Operação paper 24/7 com observações sem stake e papers com stake apenas quando o edge justificar.

## Princípios
- Zero oportunidades é um resultado válido.
- Previsões e odds são congeladas no momento da decisão.
- Closing odds servem apenas para CLV posterior.
- H2H e pequenas amostras nunca são base suficiente.
- Sem reconstrução retroativa de períodos em que o motor não estava ativo.
