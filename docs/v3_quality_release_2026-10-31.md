# V3 — melhorias operacionais e investigação (31/10/2026)

**Estado:** código, testes e execução laboratorial atualizados; uso EXCLUSIVAMENTE PAPER. Não altera retrospetivamente posições, odds ou saldos das 12 carteiras.

**Atualização operacional posterior:** os pontos 6 e 7 foram ligados ao ciclo automático, com recolha, backfill, previsão, admissão PAPER condicional e liquidação. Ver [integração de mercados e modalidades](multimarket_integration.md). O estado abaixo documenta a versão anterior; validação de vantagem continua dependente de amostra futura.

Este registo distingue o que já está **operacional** do que só pode ser **investigado/validado** antes de ativar novos mercados.

| # | Melhoria | Situação verificável | Validação / limitações |
|---|---|---|---|
| 1 | Seleção de odds elevadas | **Aplicada às NOVAS operações quantitativas e de frequência:** EV conservador com penalização por incerteza e odds extremas | Regra de sensibilidade, NÃO intervalo de confiança calibrado. Validar desempenho prospetivo |
| 2 | Concentrar risco no mesmo evento | **Aplicado:** máximo €36 em posições abertas agregadas por evento nas 12 carteiras | Posições anteriores (ex. Newcastle–Everton €42,10) preservadas, não são anuladas retroativamente |
| 3 | Calibração | **Auditoria operacional:** Brier do modelo e mercado com odds disponíveis, desdobramento por estratégia/mercado/odds | **Não validado**: reestimativa/calibração fora de amostra exige dados novos, sem otimizar no período observado |
| 4 | Odds | **Aplicado:** livros independentes sem duplicação, horário de início com diferença máxima de 30 min; exigir 2+ casas para novas entradas core | A quota da API continua 10 pedidos/dia; cobertura de futebol pode permanecer insuficiente |
| 5 | Liquidação | **Mantida/monitorizada:** resultados oficiais ESPN por ID de jogo, execução idempotente e alerta de posição aberta 5 h após começo | Sem dados oficiais, mantém-se OPEN; não inventar vencedores. Regras 1X2/ML preservadas, sem suportar mercados sem liquidação validada |
| 6 | Modelos NBA/NHL/NFL/MLB | **Código experimental disponível**; pesquisa de pontuação específica por modalidade; não utiliza modelos de futebol para ML | **NÃO ATIVADO**: insuficiente histórico cronológico e calibração para operações orientadas pelo modelo |
| 7 | Novos mercados | **Investigação:** futebol O/U 2.5, BTTS e handicap -0.5 com liquidação por golos e testes | **NÃO ATIVADO** sem odds de cada mercado com hora de captura e testes prospetivos |
| 8 | Atividade 1–3/dia | **Mantido:** meta diagnóstica por cada uma das 8 estratégias e 1–3 referências diárias, sem operações artificiais | Não garantido nos dias sem preço/modelo/EV e quando o risco agregado impede novas entradas |
| 9 | Stakes | **Aplicado às novas entradas:** mínimo €1, orçamento máximo 2% do total e máximo 0,3% (€36) por evento; carteira individual com limites existentes | Valores antigos, incluindo €0,02, permanecem como trilho de auditoria |
| 10 | Backtest | **Aplicado:** desdobramento cronológico por época além das ligas, treino anterior às previsões do próprio dia e comparação com uniforme | **Validação independente contra mercado não concluída:** faltam odds históricas ponto-no-tempo; ROI histórico/CLV não inferidos |
| 11 | 12 carteiras e painel | **Aplicado:** conta entradas reais vs eventos distintos, exposição por evento, apostas atrasadas, lucro por data do jogo e liquidação, Brier de mercado | O painel estático apenas mostra o último relatório persistido, não é live |
| 12 | Testes/monitorização | **Aplicado:** GitHub Actions CI, validação de bankroll, controlo de odds, alertas de relatório com >4 h, atraso nas posições e divergência dos saldos | O workflow de saúde é de leitura e publica alertas no GitHub; não envia avisos para o chat em tempo real |

## Evidência da execução de ponta a ponta

- GitHub Actions `Sports Intelligence V3 - Parallel Strategy Lab` deve terminar com `success`, sem perda de histórico.
- Último relatório com os campos `ledger_audit`, `aggregate_risk` e `validation_readiness`:
  https://github.com/telmix-sudo/sports-intelligence-v3/blob/main/reports/strategy_lab_latest.json
- Página pública: https://telmix-sudo.github.io/sports-intelligence-v3/strategies.html
- Health audit: https://github.com/telmix-sudo/sports-intelligence-v3/actions/workflows/health-check.yml

## Critério para ativar modelos/mercados adicionais

Não ativar pelo simples facto de aumentar a quantidade de apostas. Exigir, para cada modalidade/mercado, dados de treino validados, teste cronológico fora da amostra, cotejo contra probabilidade de mercado **sem margem**, regras de liquidação cobertas por testes e operação prospetiva em paper. Manter 12 carteiras intactas até existir uma justificação para nova carteira. Nenhum componente permite apostar dinheiro real.

As estratégias são correlacionadas em muitos jogos: 67 posições fechadas podem representar apenas 18 eventos únicos e não constituem 67 previsões independentes.
