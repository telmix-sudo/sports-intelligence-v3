# Integração operacional de mercados e modelos — 31/10/2026

Estado: integrada no laboratório automático, exclusivamente PAPER. As doze carteiras anteriores conservam o histórico e os saldos. Este documento substitui o estado «código experimental não integrado» dos pontos 6/7 do relatório de 31/10.

## Recolha que está implementada

- Odds: vencedor (`h2h`), O/U 2,5 (`totals`), handicap casa −0,5/fora +0,5 (`spreads`) e ambas marcam (`btts`, endpoint por evento).
- Pares completos do mesmo operador e linha, timestamps efetivos, nomes exatos e tolerância de início de 30 minutos. Probabilidade sem margem por casa antes do consenso. Cotações de mercados diferentes nunca são deduzidas de 1X2.
- Limite conservador de **10 créditos/dia**, não dez chamadas com mercados ilimitados. Uma região. A recolha EPL inclui três mercados numa chamada; BTTS usa até um jogo EPL por dia. NBA/NHL, NFL/MLB e duas das outras quatro ligas europeias (alternadas por dia) partilham o orçamento restante. Sem nova subscrição ou plano pago. Cabeçalhos de quota esgotada interrompem chamadas.
- O pipeline aceita falta de cobertura do fornecedor como bloqueio explícito. Não promete preços para todos os jogos/mercados. Snapshots de odds antigos não são tornados atuais pela execução do código novo.

## Modelos e testes em funcionamento

O laboratório chama `research_pipeline.run_research` em cada execução. Faz backfill incremental de resultados diários ESPN NBA/NHL/NFL/MLB, intercala dias recentes com um mês de competição da última época, mantém cache e rejeita resultados do próprio dia/futuros. Até 32 dias por modalidade em cada ciclo, oito pedidos em paralelo, com erros e contagens no relatório. Modelos de pontuação distintos por modalidade; NHL/MLB conservam a probabilidade de empate e usam uma hipótese de resolução declarada, com atualização a partir de empates de tempo regulamentar quando disponíveis. Esta aproximação exige validação própria e não constitui um modelo avançado de goalies, pitchers ou lesões.

O/U, BTTS e handicap usam Poisson de futebol com histórico casa/fora. O handicap −0,5 partilha o resultado com uma vitória caseira 1X2 e não é evidência independente.

Todos os modelos fazem previsão antes de atualizar qualquer resultado da mesma data. Os parâmetros são fixos; o último quinto das datas serve de holdout cronológico de scores. Esse teste não mede lucro histórico nem vantagem sobre bookmakers, porque não contém odds contemporâneas.

## Duas etapas distintas

1. **Validação sem stake:** cada primeiro par jogo/mercado/versão com preço auditável é gravado em `data/market_research_v3.jsonl`. Não é uma aposta. Previsão, inputs, probabilidade, odds, fonte e corte temporal ficam imutáveis. Resultados oficiais produzem entradas de liquidação separadas; cada ID é liquidado uma única vez. Jogos antigos ainda pendentes são reconsultados pela data do evento.
2. **Experiência PAPER com stake:** permitida quando há pelo menos 200 previsões históricas cronológicas, 50 no holdout, Brier/Log Loss do holdout melhores que o uniforme, ECE até 15pp, duas casas, edge ≥4pp, EV conservador >1%, jogo futuro e risco disponível. Inicia uma conta experimental separada de **1.000 € fictícios apenas na primeira posição elegível**; stake 0,25%, exposição da conta até 2%, até três posições diárias e só uma por evento. Partilha os tetos agregados existentes de €36/jogo e €240 em aberto, sem os aumentar.

Depois de pelo menos 100 eventos prospetivos por modalidade/mercado/versão, desempenho pior que o mercado bloqueia novas stakes desse grupo. Evidência estatística de vantagem é reportada separadamente: mínimo 100 eventos, período de 14 dias, comparação pareada com limite de Hoeffding, calibração e Log Loss. **Uma posição experimental não significa modelo validado.** Nenhum estado permite dinheiro real.

Snapshots adicionais de preço são append-only. CLV só é calculado quando há preço da mesma casa capturado antes do jogo e atualizado nos últimos 30 minutos; é a última cotação observada, não se afirma ser exatamente a cotação de fecho. Sem essa prova fica indisponível.

## Operação e consulta

- Scanner horário conserva propriedade exclusiva do orçamento de odds.
- Laboratório mantém o agendamento e executa também depois de um scanner concluído com sucesso, para consumir o novo cache.
- Journal, resultados históricos e relatório são persistidos no GitHub; o painel inclui previsões, liquidações, conta experimental e bloqueios.
- Relatório: `reports/market_research_latest.json`; resumo também em `reports/strategy_lab_latest.json`.
- Os testes cobrem mercados/linhas, créditos, timestamps, ida de preço a previsão e resultado, imutabilidade, repetição idempotente, limites partilhados, pares NHL e bloqueio por desempenho inferior ao mercado.

Continua dependente de dados e resultados que cheguem após a previsão. Não é possível fabricar retroativamente odds pré-jogo ou declarar vantagem antes de a amostra existir.
