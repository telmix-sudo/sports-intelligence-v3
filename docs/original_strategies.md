# Estratégias próprias V3: hipóteses pré-registadas

**Data de introdução:** 18 de outubro de 2026. Todas experimentais. Nenhuma rentabilidade futura é garantida.

Cada novo método tem uma **carteira de 1.000 € fictícios independente** e nasce com data de criação registada em `data/strategy_registry_v3.json`. As cinco carteiras anteriores e os respetivos ledgers **não recomeçam nem são apagados**. O sistema bloqueia a execução se o saldo previamente publicado divergir das transações registadas.

## 1. CONSENSUS_SHRUNK — consenso conservador

**Problema detetado:** o Elo e o Poisson atribuem probabilidades muito diferentes em certos jogos; resultados altos podem resultar de sobreconfiança e de dados escassos.

Hipótese própria:
- Combinação de 35% Elo padrão, 35% Poisson, 15% Elo rápido e 15% ajuste de placares baixos.
- Aplicar regularização de 15% para probabilidades de referência fixas (casa 43%, empate 27%, fora 30%). **Não são probabilidades calibradas**, apenas um amortecedor pré-definido.
- Pelo menos 12 jogos históricos por equipa, edge >=4 pontos percentuais, valor esperado >=2% e odds atualizadas há no máximo 8 horas.

**Avaliável historicamente** com resultados cronológicos e Brier/Log Loss. O benefício só fica demonstrado se bater benchmarks em observações futuras independentes.

## 2. DISAGREEMENT_VETO — veto por discordância

**Problema detetado:** um único modelo pode sinalizar uma odd supostamente vantajosa enquanto outros modelos identificam o adversário como favorito; selecionar o maior edge agrava falsos positivos.

Hipótese própria:
- Probabilidades da combinação fixa Elo/Poisson, iguais à carteira de referência `ELO_POISSON_BLEND`.
- **Filtro adicional**: pelo menos 3 dos 4 modelos base têm de escolher a mesma seleção como a mais provável; divergência máxima de 12 pontos percentuais na probabilidade dessa seleção.
- Pelo menos 16 jogos históricos por equipa, edge >=5 pp, EV >=4%, odds atualizadas há no máximo 4 horas.

É principalmente uma estratégia de **seleção e risco**, não uma previsão probabilística fundamentalmente nova. No histórico, só avalia a subamostra aprovada pelo filtro; resultados por jogo **não são diretamente comparáveis** aos métodos que cobrem todos os jogos.

## 3. MARKET_ANCHORED — previsão ancorada no mercado

**Problema detetado:** um modelo pode mostrar um edge artificial por erro de especificação quando enfrenta informação agregada de várias casas. A mistura com probabilidades sem margem torna-o mais conservador.

Hipótese própria:
- 70% da combinação Elo/Poisson + 30% de consenso contemporâneo de bookmakers após remoção da margem.
- Exigir pelo menos **duas casas** no consenso, 12 jogos históricos por equipa, edge >=4 pp depois da ancoragem, EV >=3% e odds atualizadas há no máximo 6 horas.
- **Não há backtest histórico válido desta estratégia** sem odds históricas contemporâneas a cada previsão; o Brier e o Log Loss históricos são representados como `null`, nunca como zero.
- Esta carteira será avaliada **apenas prospetivamente** com snapshots reais e registo de todas as entradas.

## Proteções novas

1. O ficheiro de registo de estratégias guarda `created_utc`, banca inicial, identidade e versão do método; as contas antigas têm a data inaugural documentada.
2. Os registos de transações continuam por estratégia em ficheiros JSONL independentes. As liquidações acrescentam resultado, sem alterar previsão, odd nem stake congelados.
3. Antes de uma execução, o motor reconcilia o saldo anterior com a banca inicial + lucro/prejuízo de todas as operações liquidadas. Divergências **bloqueiam** a atualização, em vez de fazerem um reset silencioso.
4. Não aceita flags de odds «com timestamp» sem três timestamps de cotação realmente presentes e recentes; rejeita probabilidades inválidas e jogos iniciados.
5. Novas estratégias só registam previsões a partir do momento da ativação, nunca retrospectivamente.
6. Todas as bancas e perdas são preservadas quando uma estratégia nova é introduzida; não existe transferência de capital entre carteiras.
7. Manter `initial_bank_per_strategy_eur = 1000` é apenas uma referência; o saldo publicado é calculado a partir dos resultados efetivamente registados.
8. Comparações retrospectivas permanecem exploratórias, e resultados positivos de carteira não provam que as odds possam ser executadas comercialmente.

### Estado atual

- Cinco métodos anteriores: mantém-se ativos e sem alterações aos parâmetros originais.
- Três métodos próprios: candidatos introduzidos para testes separados e avaliação prospetiva.
- **Oito bancas fictícias**; cada uma começa com 1.000 € apenas uma vez.
- Não executar dinheiro real. Abandonar ou ajustar apenas mediante novas versões explícitas, preservando a versão antiga no ledger.

Códigos: `src/sports_intelligence/custom_strategies.py`, `strategy_registry.py` e `strategy_lab.py`.

Painel: https://telmix-sudo.github.io/sports-intelligence-v3/strategies.html
