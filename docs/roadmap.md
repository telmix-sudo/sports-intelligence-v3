# Acompanhamento Sports Intelligence V3

Atualizado: 18/10/2026. Estado baseado nos últimos relatórios e runs confirmados, não numa promessa de cobertura futura.

## O que está feito

- [x] Repositório com permissão de escrita e código versionado.
- [x] Scanner de investigação agendado de hora em hora em GitHub Actions.
- [x] Publicação de painel estático via GitHub Pages, confirmado HTTP 200.
- [x] Ligação à The Odds API autenticada (teste pontual: 25 eventos EPL).
- [x] Fontes ESPN: calendário e resultados de 9 competições/modalidades observadas na primeira execução.
- [x] Backtest walk-forward inicial da EPL: 1.200 jogos carregados, 1.100 avaliados; Elo e Poisson comparados com mercado histórico.
- [x] CI, testes de integridade, de leakage por dia, de odds e ledger.
- [x] Relatório JSON e estado persistidos por GitHub Actions.

## Em curso ou por verificar

- [ ] Confirmar no **scanner regular** que os snapshots de odds são obtidos, validados, associados ao evento e atualizados automaticamente, com custos controlados. A verificação avulsa da chave não equivale a esta prova.
- [ ] Confirmar operação sem interrupções durante pelo menos sete dias e lidar com atrasos ocasionais do cron do GitHub.
- [ ] Implementar um processo controlado para observações com hora, modelo, probabilidade, odd real, incerteza calibrada e fonte, seguidas de liquidação e métricas prospectivas.
- [ ] Calibrar o modelo e fazer avaliação fora da amostra realmente independente. O primeiro benchmark EPL perdeu para o mercado em Brier e Log Loss.
- [ ] Alargar modalidades e mercados conforme a disponibilidade de dados; hoje apenas EPL possui modelo próprio ligado ao scanner, e nem todas as modalidades consultadas possuem odds/modelos.
- [ ] Integrar o histórico e todas as novas previsões da V2.1 sem modificar previsões anteriores; manter IDs originais e indicar origem e timestamps.
- [ ] Garantir alertas de fonte obsoleta, preços expandidos, anomalias e falhas de liquidação; testar recuperação automática.
- [ ] Paper com stake somente quando o modelo for calibrado, tiver incerteza defensável e preço de mercado verificável.
- [ ] Métricas por competição, mercado e intervalo de edge; acompanhar Brier/Log Loss, ROI, drawdown e CLV quando disponível.

## Política de transição V2.1 → V3

A tarefa ChatGPT **Sports Intelligence V2.1 — 24h** mantém-se ativa e é uma fonte de observações experimental independente.

Não desligar a V2.1 por data arbitrária. Desligar só depois de:
1. O scanner V3 ter execução estável e receber fontes e odds verificáveis.
2. A V3 registrar e liquidar autonomamente previsões prospectivas sem data leakage.
3. Todas as previsões ainda abertas da V2.1 estarem resolvidas ou migradas com rasto de auditoria.
4. O ledger V3 ter banca, exposição, observações, Brier e Log Loss coerentes e reprodutíveis.
5. A V3 ter cobertura de modalidades e mercados suficiente para substituir a função real da V2.1.
6. Passar no teste de estabilidade contínua e num período de operação em paralelo com comparação de resultados.

A validação estatística da existência de uma vantagem real sobre as odds é **um marco separado** da substituição técnica da V2.1. Não negociar com dinheiro real enquanto faltar validação.

## Ligações úteis

- [Painel público](https://telmix-sudo.github.io/sports-intelligence-v3/)
- [Execuções GitHub Actions](https://github.com/telmix-sudo/sports-intelligence-v3/actions)
- [Último relatório bruto](https://github.com/telmix-sudo/sports-intelligence-v3/blob/main/reports/latest.json)
- [Qualidade dos dados](data_quality.md)
- [Código](https://github.com/telmix-sudo/sports-intelligence-v3)

## Atualizações

| Data | Alteração confirmada |
| --- | --- |
| 18/10/2026 | Criado repositório V3 e motor de investigação com relatórios. |
| 18/10/2026 | GitHub Pages publicado; autenticação da Odds API verificada separadamente. |
| 18/10/2026 | Primeiro backtest EPL não supera o benchmark de mercado. Manter o modelo experimental. |
