# Auditoria de qualidade dos dados — Sports Intelligence

## V1 (setembro de 2026)
O histórico V1 não constitui validação de edge nem backtest. Apenas cinco posições.

- Paper 001: odd **~1,63** era aproximada; campo numérico da folha usa 1,63 como referência contabilística, **não** como cotação exata verificável; hora de início não preservada.
- Paper 002: na previsão original a odd foi registada como **intervalo 1,36–1,37**, tendo-se usado conservadoramente **1,36 para liquidação**. Não declarar o valor 1,36 como odd originalmente congelada exata.
- Papers 003–005: probabilidades e odds permanecem as registadas; não corrigir retroativamente após jogos.
- CLV da V1 não está disponível. Quaisquer métricas OOS exigem avaliações prospetivas novas.
- Não reconstruir o hiato de execução entre 15/09 e 17/10 como se tivessem existido previsões na altura.

## V2.1 (outubro de 2026)
Três observações NFL iniciais encontram-se preservadas em `data/observations_v2_1.csv`, **sem stake**. Os estados dessa tarefa ChatGPT não são automaticamente gravados pelo motor GitHub V3; nunca misturar ambos os ledgers sem uma migração auditada.

## V3
- `reports/latest.json` é o resultado confirmado da execução GitHub Actions.
- `data/forecasts_v3.jsonl` é o ledger de previsões V3 congeladas e liquidações subsequentes.
- `data/odds_cache_v3.json` guarda apenas agregados mínimos de cotações, nunca respostas brutas do fornecedor.
- Uma previsão não é publicada como paper com stake sem modelo adequadamente validado e odd pré-jogo atual.
- Football-Data.co.uk fornece CSV com dados históricos; alguns preços são de fecho e só servem como benchmark retrospetivo.
- ESPN é utilizado para descobrir calendário/resultados, não como prova de odds ou cobertura completa de todos os desportos.
- Jeff Sackmann ATP tem licença CC BY-NC-SA; importação opcional e apenas para investigação não comercial.

## Problemas e decisões em aberto
- O modelo Elo inicial foi comparado com o mercado no histórico 2023/24–2026/27: pior Brier/Log Loss no primeiro benchmark. Não alegar vantagem.
- API gratuita de odds não configurada: não haverá previsões com valor verificável; o scanner continua a detetar calendários e a medir benchmarks.
- Sem Cloudflare Pages publicado; o serviço operacional é o workflow GitHub Actions.

## Laboratório paralelo (18/10/2026)
- **Alteração de fonte**: a rotina automatizada deixou de recolher Football-Data.co.uk, cujas condições atuais restringem recolha por bots/IA e aplicações comerciais. O benchmark anterior de 1.100 jogos ficou documentado como histórico experimental, não deve ser novamente descarregado automaticamente.
- Os cinco modelos paralelos usam exclusivamente resultados OpenFootball (CC0/domínio público) e, quando presentes, odds em tempo real com timestamp da The Odds API. A comparação histórica não contém preço retrospetivo de execução e por isso não tem ROI calculável.
- As bancas de 1.000 € por método são **contas virtuais independentes**. A rentabilidade é 0 apenas porque ainda não foram feitas apostas paper liquidadas; ausência de dados nunca é convertida em lucro.
- Cinco variantes comparadas sobre os mesmos dados podem ter diferenças aparentes por variância. A melhor pontuação histórica não equivale a validação de edge nem garante desempenho futuro.
- O relatório auditável encontra-se em reports/strategy_lab_latest.json e no painel público strategies.html.
