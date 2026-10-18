# Sports Intelligence V3

Sistema de investigação desportiva **exclusivamente em paper trading**. Modelos experimentais, comparação no-vig, backtesting cronológico e ledger auditável. **Nenhuma aposta com dinheiro real.**

## Já operacional

- GitHub Actions: [scanner programado de hora a hora](https://github.com/telmix-sudo/sports-intelligence-v3/actions/workflows/scanner.yml) e [testes automáticos](https://github.com/telmix-sudo/sports-intelligence-v3/actions/workflows/ci.yml).
- [Relatório mais recente](reports/latest.json) e [histórico de execução](reports/runs/).
- [Painel principal publicado](https://telmix-sudo.github.io/sports-intelligence-v3/) e [comparador de oito carteiras](https://telmix-sudo.github.io/sports-intelligence-v3/strategies.html).
- Calendário de 9 competições com fonte ESPN e controlo de estados/timezones.
- O scanner regular usa agora resultados OpenFootball (domínio público/CC0) para EPL. O histórico anterior Football-Data foi descontinuado na recolha automatizada devido às restrições de utilização do fornecedor.
- [Laboratório paralelo](reports/strategy_lab_latest.json), agendado duas vezes por dia: oito estratégias (cinco anteriores e três próprias) e oito carteiras fictícias isoladas em cinco ligas europeias.
- Importação ATP Jeff Sackmann opcional, **desativada por defeito** devido à licença CC BY-NC-SA.
- Registo prospetivo V3 em [data/forecasts_v3.jsonl](data/forecasts_v3.jsonl), criado quando existirem observações admissíveis.
- Se faltarem odds, dados ou modelo fiável, o sistema **não inventa oportunidades**.

## Estado de validação

No benchmark legado EPL (1.100 jogos) o Elo ficou **abaixo do mercado**. A partir de 18/10 o laboratório paralelo usa OpenFootball e já testou **3.733 jogos** para cada uma de cinco estratégias. Como esses dados históricos não trazem odds contemporâneas, o resultado é de calibração e scoring (Brier/Log Loss), **não de lucro ou vantagem sobre o mercado**. Comparar modelos num mesmo conjunto exploratório não valida o vencedor OOS.

V1: 5 papers, 4 vitórias e 1 derrota; P/L contabilístico +0,1644 € a partir de 1.000 €. Consulte [qualidade dos dados](docs/data_quality.md): pelo menos duas cotações antigas não foram congeladas com precisão suficiente para auditoria perfeita.

V2.1: tarefa ChatGPT separada, com três observações NFL iniciais. **Ainda não sincroniza automaticamente** a sua evolução com o GitHub. Não misturar métricas V2.1 e V3.

V3: scanner geral e oito contas de estratégias em paper independente. No laboratório, pequenas stakes **puramente fictícias** só podem surgir se houver hora do jogo, odds válidas antes do início, edge suficiente e regras de risco. **Não há autorização para apostas com dinheiro real.**

## Odds e conta de API (já configurada no GitHub)

O feed ESPN que foi testado fornece jogos, mas **não disponibilizou 1X2/ML completas** nesta varredura. Foi por isso criado suporte a [The Odds API](https://the-odds-api.com/) (plano Starter de **500 créditos/mês** indicado pelo fornecedor).

1. A autenticação da API foi confirmada num teste pontual a 18/10/2026, sem revelar a chave.
2. O orçamento máximo atual é de **10 chamadas/dia**: EPL, La Liga, Bundesliga, Serie A e Ligue 1 às 08 UTC; EPL, NFL, NBA, NHL e MLB às 20 UTC. Um mercado `h2h`, região `eu`.
3. Guardar a chave apenas como Secret `ODDS_API_KEY` no GitHub; nunca em chat, código ou relatórios.
4. Uma autenticação bem-sucedida não prova que a recolha agendada de odds foi persistida; confirmar `reports/latest.json` após o próximo ciclo.

A descoberta de eventos continua horária mesmo sem chave. O API de odds usa orçamento próprio para não ultrapassar o plano gratuito.

## Publicar o painel sem custos

O GitHub Pages já foi ativado e testado: [painel principal](https://telmix-sudo.github.io/sports-intelligence-v3/) e [laboratório](https://telmix-sudo.github.io/sports-intelligence-v3/strategies.html).

## Executar localmente

Requer Python 3.11+; sem dependências externas.

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
PYTHONPATH=src python -m sports_intelligence.cli --mode backtest
PYTHONPATH=src python -m sports_intelligence.cli --mode scan
PYTHONPATH=src python -m sports_intelligence.strategy_lab
```

`ENABLE_ATP_RESEARCH=1` ativa apenas o dataset ATP para uso de investigação permitido pela licença; nunca copiar o dataset bruto para este repositório.

## Fontes e métodos

- ESPN scoreboard: eventos, competição, resultados, kickoff UTC; API pode não ter todos os mercados, serviço sujeito a alterações.
- OpenFootball/football.json (CC0): cinco ligas, dados públicos e resultados por data. Não voltar a executar bot sobre Football-Data.co.uk sem autorização explícita.
- Odds (quando configuradas): benchmark no-vig calculado **dentro de cada bookmaker**, consenso entre casas, melhor preço documentado por seleção e hora de atualização. Apenas cotações frescas.
- Métricas: Brier, Log Loss, comparação com benchmark, P/L paper, yield e drawdown quando aplicável.
- Nunca usar informação obtida após o início de um evento para reescrever previsões pré-jogo.
- Não forçar posições para melhorar artificialmente a frequência.

## Arquitetura

```text
src/sports_intelligence/
  feeds.py           dados ESPN e utilitários de aquisição
  football.py        Elo prequential
  poisson.py         Poisson com shrinkage
  tennis.py          Elo ATP não comercial (opcional)
  odds_provider.py   odds via segredo opcional e budget
  scanner.py         eventos, candidatos e observações
  metrics.py         Brier, Log Loss, no-vig
  strategy_lab.py    cinco estratégias, backtest e cinco carteiras isoladas
  audit.py           reconciliação do ledger antigo
  cli.py             orquestração e relatórios
data/                ledger histórico, observações, cache e estado
reports/             relatório atual e arquivo horário
docs/                painel e qualidade dos dados
tests/               testes cronológicos, de risco, odds e integridade
.github/workflows/    CI e operação automática
```

Para informação detalhada consulte [investigação e estratégias paralelas](docs/strategy_research.md), [configuração V3](config/system_v3.md) e [auditoria das limitações](docs/data_quality.md).

## Evolução do laboratório sem reiniciar bancas

Em 18/10/2026 foram adicionadas três estratégias próprias: **CONSENSUS_SHRUNK**, **DISAGREEMENT_VETO** e **MARKET_ANCHORED**. Cada estratégia entra com exatamente **1.000 € fictícios**, apenas no dia da criação. As cinco carteiras iniciais **mantêm o seu saldo e o ledger**, incluindo lucros e perdas anteriores.

O mecanismo de integridade em `strategy_registry.py` compara, antes de cada execução, o saldo anteriormente publicado com as transações da respetiva estratégia; qualquer discrepância interrompe a atualização para não apagar dinheiro fictício. Consultar a [metodologia e parâmetros das três estratégias próprias](docs/original_strategies.md) e o [painel de acompanhamento das oito carteiras](https://telmix-sudo.github.io/sports-intelligence-v3/strategies.html).
