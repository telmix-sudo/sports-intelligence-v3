# Integração operacional de mercados e modelos — 31/10/2026

Estado: integrada no laboratório automático, exclusivamente PAPER. As doze carteiras anteriores conservam o histórico e os saldos. Este documento substitui o estado «código experimental não integrado» dos pontos 6/7 do relatório de 31/10.

## Recolha que está implementada

- Odds: vencedor (`h2h`), O/U 2,5 (`totals`), handicap casa −0,5/fora +0,5 (`spreads`) e ambas marcam (`btts`, endpoint por evento).
- Pares completos do mesmo operador e linha, timestamps efetivos, nomes exatos e tolerância de início de 30 minutos. Probabilidade sem margem por casa antes do consenso. Cotações de mercados diferentes nunca são deduzidas de 1X2.
- Limite conservador de **10 créditos/dia**, não dez chamadas com mercados ilimitados. Uma região. A recolha EPL inclui três mercados numa chamada; BTTS usa até um jogo EPL por dia. NBA/NHL/MLB são consultados às 20 UTC, NFL e duas das outras quatro ligas europeias (alternadas por dia) às 14 UTC. EPL às 08 UTC; BTTS após obter um ID futuro. A janela noturna foi deslocada das 02 para as 20 UTC para evitar odds caducadas antes dos jogos da noite. Sem nova subscrição ou plano pago. Cabeçalhos de quota esgotada interrompem chamadas.
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


## Atualização de atividade e mercados adicionais — 31/10/2026

A pedido do utilizador, a meta passa a **5–10 apostas PAPER elegíveis por dia em TODAS as bancas ativas**, incluindo as três antigas estratégias de frequência (agora contadas por dia, não por semana), referência e conta experimental quando criada. O máximo diário é 10. As novas stakes base são 0,25%; saldos, stakes e previsões antigos não são reescritos. O limite de 2% por conta e os tetos agregados de €36/jogo e €240 em aberto continuam em vigor, pelo que é preciso diversificar eventos e reutilizar exposição após liquidações. Frequência não prova vantagem nem permite inventar operações.

O motor mantém os modelos de vencedor e acrescenta um componente auxiliar comum explicitamente identificado, disponível a todas as carteiras: diferentes linhas de golos, totais/handicaps de cantos e outros mercados binários com estatística, preço e regras verificáveis. A mesma previsão repetida entre carteiras não passa a ser evidência independente. São permitidos até dois mercados por evento/conta; duplicação de uma vitória caseira como handicap −0,5 é rejeitada.

O catálogo é descoberto no endpoint de mercados do evento. Faz-se recolha incremental de estatísticas oficiais ESPN (cantos, foras de jogo, faltas, remates e cartões quando presentes), modelos de contagem específicos e teste cronológico por estatística/linha. Não são usadas probabilidades de golos para cantos. A liquidação consulta o número oficial da estatística; o resultado final em golos não liquida uma aposta de cantos.

O orçamento máximo passa a **16 créditos por dia** (até 496 em 31 dias), dentro do plano gratuito de referência de 500/mês e sempre sujeito à quota restante comunicada pelo fornecedor. Não foi comprada qualquer API/subscrição. A discovery custa um crédito e a recolha adicional usa até seis mercados num evento; um mercado pode devolver muitas linhas. Cobertura não é exaustiva.

**Limitações explícitas:** o fornecedor atual anuncia cantos/cartões mas não mercados de foras de jogo, lançamentos laterais ou faltas. Esses dados estatísticos são recolhidos, mas não se inventam odds para abrir apostas. Mercados de cartões/bookings dependem de regras inequívocas por operador (contagem vs pontos); sem essa prova são bloqueados. Períodos, jogadores e linhas com devoluções/quarters não são convertidos em binários incorretos. Mercados presentes no catálogo mas sem modelo/regra validada são identificados como bloqueios. O painel mostra todas as carteiras abaixo de cinco entradas e o motivo da insuficiência.

## Correções de operacionalização — 02/11/2026

Esta atualização substitui a recolha adicional descrita acima: reserva até quatro dos mesmos 16 créditos diários para pedir diretamente `alternate_totals_corners`, `alternate_totals` e `btts` no endpoint de odds do evento. Não é necessário gastar primeiro um crédito a listar mercados. As tentativas e o orçamento são persistidos; uma tentativa anterior num jogo não bloqueia os restantes. Preços de dois operadores e timestamps originais continuam obrigatórios.

As correspondências de clubes são exatas: nomes oficiais presentes em OpenFootball, como `US Sassuolo Calcio`, `ACF Fiorentina`, `FC Lorient` e `Stade Brestois 29`, passam a coincidir com os nomes apresentados pela ESPN. Não se removem prefixos genericamente nem se aproximam nomes ambíguos como `Deportivo`. Fontes de nomes: `openfootball/football.json`, ficheiros `2025-26/it.1.json`, `fr.1.json`, `es.1.json` e `de.1.json`; nomes de apresentação nos registos oficiais ESPN.

Totais e handicaps de linha inteira usam probabilidades condicionais a um resultado sem devolução e conservam separadamente a probabilidade de push. O EV inclui a fração de probabilidade que não é devolvida; um resultado exatamente na linha liquida como VOID, com lucro zero. Linhas de quarto, períodos e regras de cartões não implementados continuam bloqueados. Não se modela uma devolução como derrota.

O backfill de estatísticas usa datas de jogos efetivamente concluídos no histórico dos dois anos anteriores, até oito datas por liga ativa e 120 resumos por execução, com cache e oito pedidos em paralelo. Os limites de validação não foram reduzidos. Uma previsão anterior sem stake pode originar uma nova posição PAPER quando satisfaz os critérios: a previsão original mantém-se intocada e a nova posição identifica a sua origem.

Golos, ambas marcam e estatísticas qualificadas são avaliados antes da fila de vencedor para que esta não esgote os dez lugares diários ou a exposição. Sem oportunidade qualificada, a procura de vencedor continua. As primeiras cinco entradas qualificadas usam aproximadamente 0,10% da banca fictícia, mínimo 1 € e nunca acima do anterior teto de 0,25%; as seguintes mantêm 0,25%. Mantêm-se os tetos de 2%/conta, 36 €/jogo e 240 € agregados, sem alterar posições antigas.

O novo campo `market_activity` e a tabela «Utilização efetiva por mercado» distinguem preços disponíveis, observações sem stake e posições PAPER abertas/liquidadas. Existir código ou preços para um mercado não significa que já existam apostas nele. A evolução operacional é confirmada pelos relatórios de execução, não apenas pelos testes.

Referência da API: https://the-odds-api.com/liveapi/guides/v4/ e https://the-odds-api.com/sports-odds-data/betting-markets.html.

Se os cantos europeus tiverem apenas uma casa, pode ser usado no máximo mais um crédito diário para consultar esse mercado na região UK, retirado da cobertura de vencedor e sempre dentro dos mesmos 16 créditos. As linhas só são unidas por identidade exata. Variantes regionais do mesmo operador não contam como duas casas; os timestamps originais de cada cotação permanecem preservados. São preços para uma simulação, não confirmação de acesso ou execução numa casa portuguesa.
