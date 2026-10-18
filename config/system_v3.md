# Sports Intelligence V3 — Arquitetura alvo

## Objetivo
Transformar o projeto num sistema persistente, auditável e testável, independente do contexto de uma conversa.

## Componentes
1. Ingestão de eventos, odds e estatísticas.
2. Normalização e controlo de qualidade.
3. Modelos simples por modalidade.
4. Motor de probabilidades e incerteza.
5. Motor de mercado/no-vig/edge.
6. Filtro de risco e seleção.
7. Ledger persistente.
8. Backtesting cronológico/walk-forward.
9. Métricas e relatórios.
10. Operação horária em paper trading.

## Fonte única de verdade
O GitHub deve guardar configuração, código, histórico congelado, datasets pequenos/derivados e relatórios.
Dados grandes podem ser armazenados externamente, mantendo manifestos e hashes no repositório.

## Regras de validação
- Split temporal, nunca random quando houver dependência temporal.
- Sem informação posterior ao cutoff da previsão.
- Benchmarks simples antes de modelos complexos.
- Só promover complexidade quando houver melhoria OOS real.
- Avaliar por modalidade, competição, mercado e faixa de edge.
