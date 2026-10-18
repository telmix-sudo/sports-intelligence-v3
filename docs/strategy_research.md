# Laboratório V3 — estratégias paralelas

Início: 18/10/2026. Investigação quantitativa; nunca são executadas apostas reais.

## Pesquisa de modelos, plataformas e estudos

| Fonte | Evidência verificável | Aplicação |
| --- | --- | --- |
| Dixon & Coles (1997) | Poisson de futebol com correção a resultados baixos; retornos históricos positivos na amostra original | Testar correção de baixos resultados sem assumir rentabilidade atual |
| penaltyblog | Biblioteca estatística de Poisson, bivariada, Dixon–Coles e decaimento temporal | Guia para comparação de modelos; dependências opcionais apenas se melhorarem OOS |
| Elo | Sistema simples de rating e evolução das forças das equipas | Benchmark lento versus rápido |
| Poisson com shrinkage | Modelação de golos por ataque/defesa em casa/fora | 1X2, futuramente totais/BTTS com odds disponíveis |
| Combinação de modelos | Ensemble fixo 50% Elo + 50% Poisson | Testar se reduz Brier/Log Loss |
| Simon (2024), Management Science | Dados de 3.681 jogos MLB e movimento de linhas, com oportunidades na amostra estudada | Fila de testes quando houver odds históricas com timestamps |
| Wheatcroft (2020), International Journal of Forecasting | Rating de ataque/defesa e análise do over/under 2,5 em dez ligas | Fila de testes para totais, com odds e custos reais |
| Angelini & De Angelis (2019) | Estudo com 41 bookmakers e mercados de eficiência variável | Procurar melhor preço e comparar mercado sem margem |
| OddsJam, RebelBetting, Trademate | Ferramentas comerciais de identificação de valor e monitorização de preços | Referências metodológicas, nunca importar ROI publicitário como lucro auditado |

Fontes académicas e software:
- https://doi.org/10.1111/1467-9876.00065
- https://penaltyblog.readthedocs.io/en/latest/models/overview.html
- https://doi.org/10.1287/mnsc.2022.00456
- https://doi.org/10.1016/j.ijforecast.2019.11.001
- https://doi.org/10.1016/j.ijforecast.2018.07.008
- https://doi.org/10.1016/j.ijforecast.2019.08.009
- https://github.com/openfootball/football.json

## Cinco estratégias independentes

| ID | Método pré-registado | Banca experimental |
| --- | --- | --- |
| ELO_STANDARD | Elo K20, +65 casa, 26% empates | 1.000 € |
| ELO_DYNAMIC | Elo K36, +65 casa, 26% empates | 1.000 € |
| POISSON_SHRINK | Poisson por equipa, 8 pseudo-jogos | 1.000 € |
| DIXON_COLES_LOW | Correção pontuações baixas, rho=-0,07 fixo | 1.000 € |
| ELO_POISSON_BLEND | Média fixa 50% Elo + 50% Poisson | 1.000 € |

DIXON_COLES_LOW é apenas uma versão de sensibilidade baseada na correção de baixa pontuação, **não** replica estimação MLE Dixon–Coles original.

Cada estratégia tem seu ficheiro imutável de previsões com campos de liquidação adicionados posteriormente e sua banca independente em data/strategy_ledgers/ e data/strategy_accounts_v3.json.

## Dados e método

- Dados históricos OpenFootball football.json sob CC0/domínio público; ligas EPL, La Liga, Bundesliga, Serie A, Ligue 1; épocas 2021/22–2026/27 disponíveis.
- Teste prequential por data, aquecimento de 100 jogos **por liga**, avaliação a partir de agosto de 2024. Sem usar resultados do mesmo dia para o próprio dia.
- Métricas Brier e Log Loss; referência trivial uniformes 1/3 é explicitamente um benchmark fraco, **não** é o mercado.
- Fonte pública não inclui odds históricas disponíveis no momento da previsão. Portanto ROI histórico fica indisponível, nunca é inventado.
- Comparar estratégias neste conjunto NÃO fornece teste final independente para o método que parecer vencedor.

## Regras do paper experimental

- Nunca conta real: saldo inicial fictício de 1.000 € **em cada carteira**.
- Até 0,5% de stake por posição, 2% de exposição máxima simultânea, quatro entradas novas/dia por carteira.
- Só registar nova posição se houver evento FUTURO, nome rigorosamente reconciliado, hora UTC, 1X2 completo, preços com fonte e timestamp válido, pelo menos oito jogos históricos para cada equipa, edge >=4 pp e EV calculado >=2%.
- Cada carteira pode escolher o mesmo evento: comparar resultados por método, **não somar as cinco como apostas independentes**.
- Todos os resultados são simulados e probabilidades permanecem EXPERIMENTAIS até provarem calibração e qualidade no futuro.
- Sem odd -> sem paper. Sem dados -> rejeição. Não reconstruir a posteriori.
- Resolver com resultados confirmados e calcular ROI/estaca, Brier, Log Loss e exposição separadamente.

## Próximos candidatos ao laboratório

- O/U 2,5, BTTS, handicap e mercados de golos via taxas Poisson e odds adequadas.
- Dixon–Coles completo com aprendizagem cronológica; bivariada Poisson.
- Modelos superfície Elo ténis, respeitando licenças e cutoff.
- NFL/nflverse e MLB com modelos contextuais próprios e preços históricos verificáveis.
- Modelos de discrepância de closing line e movimento do preço, apenas após armazenamento cronológico de séries com timestamps.

Critérios de promoção/suspensão: diferenças Brier/Log Loss face a mercado, calibração, resultado prospetivo, CLV quando disponível, custos e tamanho efetivo da amostra. Marketing de terceiros não é evidência de lucro auditável.
