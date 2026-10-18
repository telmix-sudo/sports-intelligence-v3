# Sports Intelligence V3

Sistema de investigação desportiva **exclusivamente em paper trading**. Modelos experimentais, comparação no-vig, backtesting cronológico e ledger auditável. **Nenhuma aposta com dinheiro real.**

## Já operacional

- GitHub Actions: [scanner programado de hora a hora](https://github.com/telmix-sudo/sports-intelligence-v3/actions/workflows/scanner.yml) e [testes automáticos](https://github.com/telmix-sudo/sports-intelligence-v3/actions/workflows/ci.yml).
- [Relatório mais recente](reports/latest.json) e [histórico de execução](reports/runs/).
- [Painel HTML de consulta](docs/index.html); pode ser publicado gratuitamente via GitHub Pages quando Pages estiver ativado.
- Calendário de 9 competições com fonte ESPN e controlo de estados/timezones.
- Backtest de futebol com CSV reais Football-Data.co.uk, modelos Elo e Poisson, comparação com odds históricas disponíveis.
- Importação ATP Jeff Sackmann opcional, **desativada por defeito** devido à licença CC BY-NC-SA.
- Registo prospetivo V3 em [data/forecasts_v3.jsonl](data/forecasts_v3.jsonl), criado quando existirem observações admissíveis.
- Se faltarem odds, dados ou modelo fiável, o sistema **não inventa oportunidades**.

## Estado de validação

O primeiro benchmark Elo, feito com 1.100 jogos cronológicos EPL, ficou **abaixo do benchmark do mercado**. Não existe edge validado. O modelo alternativo Poisson é investigacional e é comparado separadamente no relatório corrente. A seleção entre modelos com base nos mesmos resultados históricos não constitui prova OOS independente.

V1: 5 papers, 4 vitórias e 1 derrota; P/L contabilístico +0,1644 € a partir de 1.000 €. Consulte [qualidade dos dados](docs/data_quality.md): pelo menos duas cotações antigas não foram congeladas com precisão suficiente para auditoria perfeita.

V2.1: tarefa ChatGPT separada, com três observações NFL iniciais. **Ainda não sincroniza automaticamente** a sua evolução com o GitHub. Não misturar métricas V2.1 e V3.

V3: scanners, métricas e estados operacionais guardados em GitHub. Sem stakes automáticas enquanto não existir probabilidade calibrada e odd pré-jogo fiável.

## Ativar cotações (opcional, gratuito)

O feed ESPN que foi testado fornece jogos, mas **não disponibilizou 1X2/ML completas** nesta varredura. Foi por isso criado suporte opcional a [The Odds API](https://the-odds-api.com/) (plano Starter de **500 créditos/mês** indicado pelo fornecedor).

1. Criar conta Starter gratuita; receber a chave. Não introduzir a chave em chats, Issues ou ficheiros públicos.
2. Abrir [GitHub Secrets → Actions](https://github.com/telmix-sudo/sports-intelligence-v3/settings/secrets/actions).
3. Em **New repository secret**, escolher nome `ODDS_API_KEY` e valor igual à chave privada da API.
4. O motor fará até **10 chamadas/dia** (cinco modalidades, duas recolhas diárias UTC às 08:00 e 20:00), um mercado `h2h`, uma região `eu`. Estimativa até ~300 créditos num mês de 30 dias; confirmar consumo real no fornecedor.
5. A chave é utilizada apenas no servidor GitHub Actions e nunca é gravada em relatórios/ficheiros. Cotações sem data de atualização válida são ignoradas.

A descoberta de eventos continua horária mesmo sem chave. O API de odds usa orçamento próprio para não ultrapassar o plano gratuito.

## Publicar o painel sem custos

Abrir [Settings → Pages](https://github.com/telmix-sudo/sports-intelligence-v3/settings/pages), escolher **Deploy from a branch** → `main` → `/docs` → Save. Uma vez ativo, o endereço esperado é `https://telmix-sudo.github.io/sports-intelligence-v3/`. Só considerar publicado após confirmar que a página realmente abre.

## Executar localmente

Requer Python 3.11+; sem dependências externas.

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
PYTHONPATH=src python -m sports_intelligence.cli --mode backtest
PYTHONPATH=src python -m sports_intelligence.cli --mode scan
```

`ENABLE_ATP_RESEARCH=1` ativa apenas o dataset ATP para uso de investigação permitido pela licença; nunca copiar o dataset bruto para este repositório.

## Fontes e métodos

- ESPN scoreboard: eventos, competição, resultados, kickoff UTC; API pode não ter todos os mercados, serviço sujeito a alterações.
- Football-Data.co.uk: dados EPL de 2023/24 até à época atual. Treino cronológico, agrupamento por data: resultados do próprio dia nunca influenciam previsões do mesmo dia.
- Odds (quando configuradas): benchmark no-vig calculado **dentro de cada bookmaker**, consenso entre casas, melhor preço documentado por seleção e hora de atualização. Apenas cotações frescas.
- Métricas: Brier, Log Loss, comparação com benchmark, P/L paper, yield e drawdown quando aplicável.
- Nunca usar informação obtida após o início de um evento para reescrever previsões pré-jogo.
- Não forçar posições para melhorar artificialmente a frequência.

## Arquitetura

```text
src/sports_intelligence/
  feeds.py           recolha ESPN e Football-Data
  football.py        Elo prequential
  poisson.py         Poisson com shrinkage
  tennis.py          Elo ATP não comercial (opcional)
  odds_provider.py   odds via segredo opcional e budget
  scanner.py         eventos, candidatos e observações
  metrics.py         Brier, Log Loss, no-vig
  audit.py           reconciliação do ledger antigo
  cli.py             orquestração e relatórios
data/                ledger histórico, observações, cache e estado
reports/             relatório atual e arquivo horário
docs/                painel e qualidade dos dados
tests/               testes cronológicos, de risco, odds e integridade
.github/workflows/    CI e operação automática
```

Para informação detalhada consulte [configuração V3](config/system_v3.md) e [auditoria das limitações](docs/data_quality.md).
