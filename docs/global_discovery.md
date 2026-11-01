# Pesquisa global V3

A descoberta deixou de depender de uma lista fixa de nove competições. Mantêm-se os IDs antigos, os saldos, os ledgers e as regras de execução PAPER.

| Fonte | Descoberta | Atualização e limites |
|---|---|---|
| The Odds API | Catálogo completo, incluindo modalidades fora de época; calendário de todas as competições ativas | Catálogo a cada 6h; eventos com cache de 50min. Ambos os endpoints custam zero créditos de utilização. Política/entretenimento excluídos. |
| ESPN | Catálogo de modalidades e competições, calendários públicos | Catálogo diário; até 96 competições/ciclo, com rotação por antiguidade e diversidade. Eventos ainda futuros de rotas diferidas são mantidos até 24h. |
| TheSportsDB gratuito | Catálogo e amostra diária por modalidade | Cobertura explicitamente parcial: catálogo observado com apenas duas categorias e amostra máxima de três eventos/consulta. Cache de 3h, no máximo 26 chamadas/ciclo, espaçadas 2,2s. |

O teste público inicial encontrou 17 grupos desportivos e 359 competições na ESPN. Não é um limite codificado: novos grupos do catálogo entram automaticamente. O fornecedor de odds pode acrescentar, por exemplo, modalidades ausentes na ESPN. Competições sem calendário compatível são registadas como indisponíveis, sem inventar jogos.

## Preços e mercados

Os preços partilham o cache e o limite existente de **16 créditos/dia**. São libertados até quatro créditos por janela de seis horas; um ciclo atrasado pode usar as janelas anteriores. As competições já suportadas pelo laboratório têm prioridade; dentro desse grupo e depois nas restantes, favorecem-se modalidades ainda não cotadas e competições menos recentemente cotadas. Competições com modelo podem renovar os preços noutra janela do mesmo dia, sem repetir pedidos na mesma janela. Só são pagos preços de competições com confrontos nas próximas 12h. Até três créditos ficam reservados para a descoberta e recolha de mercados adicionais de um evento de futebol suportado.

Um crédito não equivale sempre a uma chamada: mercados distintos e regiões multiplicam o custo. A V3 usa uma região, contabiliza antecipadamente o custo e respeita o saldo comunicado pelo fornecedor. A descoberta gratuita continua quando o orçamento de preços termina. Não há subscrição ou aumento automático do plano.

As 16 unidades diárias não permitem atualizar preços de todas as competições todos os dias. A pesquisa de calendários tem cobertura maior do que a de odds. Datas de atualização dos bookmakers são preservadas; a hora de leitura do cache nunca é apresentada como uma nova cotação.

Atualizações parciais preservam outras cotações ainda válidas. O scanner e o laboratório verificam os timestamps efetivos de todas as seleções; a atualização de outro mercado não rejuvenesce preços antigos. Preços com timestamps conhecidos já expirados saem da cache ativa. As cotações congeladas das apostas e observações continuam nos ledgers e diários de investigação; estes registos de auditoria não são podados.

## Integridade e interpretação

- A janela de descoberta é de 48h, com timestamps que incluem fuso horário. Eventos sem hora comprovável não são transformados em entradas pré-jogo.
- Eventos de torneio/corrida com vários participantes são distintos de confrontos entre dois participantes.
- Eventos de fontes diferentes só são unidos quando os nomes normalizados dos dois participantes e a modalidade coincidem, com início até 30min de diferença. Aliases desconhecidos podem continuar como eventos separados; o total não é garantia de jogos únicos entre todas as fontes.
- Competições novas de futebol exigem o mercado 1X2 completo. Um mercado com empate não é normalizado como binário.
- Modalidades novas não recebem probabilidades do modelo de futebol nem habilitação automática para abrir posições. A carteira de referência também rejeita eventos marcados como não elegíveis para execução.
- Os modelos, validação cronológica e adaptação de resultados continuam a determinar quais os mercados que podem gerar previsões e posições. A meta 5–10/dia permanece dependente de oportunidades elegíveis.
- Falhas de fontes e modalidades apenas catalogadas ficam visíveis. Não se confundem modalidades catalogadas, consultadas, com jogos, com preços ou com modelo.

## Artefactos e operação

`reports/global_discovery_latest.json` apresenta cobertura por modalidade, consultas, falhas e alocação de créditos. `data/global_events_v3.json` contém o calendário completo; a pré-visualização de 50 candidatos do relatório principal não limita o painel. `data/global_discovery_cache_v3.json` permite a rotação entre execuções.

O scanner horário persiste os três ficheiros no repositório; o laboratório incorpora uma referência ao relatório global. A monitorização verifica frescura, persistência e consistência de timestamps. A chave da API continua apenas no secret GitHub e não é escrita em relatórios ou caches.

Documentação primária: [The Odds API V4](https://the-odds-api.com/liveapi/guides/v4/), [catálogo ESPN](https://sports.core.api.espn.com/v2/sports?limit=1000), [TheSportsDB](https://www.thesportsdb.com/documentation).
