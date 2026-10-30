# Meta de atividade diária — V3

**Introduzida em 27/10/2026, hora de Portugal.**

## Objetivo

Procurar **1 a 3 eventos diferentes com posições fictícias por dia civil de Portugal**, sempre que existam eventos futuros com odds genuínas, completas, atuais e verificáveis. Não é possível garantir a meta em dias sem eventos elegíveis. Não confundir frequência de testes com evidência de valor.

Os oito modelos anteriores continuam sob as suas próprias regras de edge e EV, sem entrada forçada. Para medir o que acontece quando se tenta manter atividade diária, há uma nona carteira totalmente separada denominada `MARKET_DAILY_REFERENCE`.

## Market Daily Reference

- Nova banca **1.000 € fictícios**. Os saldos de todas as outras carteiras ficam intactos.
- Seleção de referência: **favorito do mercado** pelo consenso das probabilidades *no-vig* de pelo menos duas casas. Não afirma que o favorito tem valor esperado positivo.
- Modalidades: futebol europeu (mercado 1X2), NFL, NBA, NHL e MLB (mercado moneyline).
- Apenas jogos ainda não iniciados e até 48h do início; odds reais da The Odds API com timestamp da própria cotação de, no máximo, 8 horas.
- Até **três entradas de referência por dia civil de Lisboa**, em **eventos distintos**. Não duplicar o mesmo evento na mesma carteira nem ultrapassar 2% de exposição em aberto.
- Stake: 0,5% da banca fictícia da própria carteira, exposição em aberto limitada a 2%; se o limite estiver atingido, a entrada é rejeitada.
- Em dinheiro real não existe qualquer ligação a corretora ou casa de apostas e nenhuma operação é executada.
- Depois do jogo, resultado oficial confirma a liquidação: vitória gera lucro fictício segundo a odd registada, derrota perde a stake, empate em moneyline de dois resultados é considerado anulado (VOID) sem prejuízo fictício.
- Guarda hora de previsão, event ID, casa, mercado, preço, probabilidade, stake e origem. Contagens por dia são calculadas pela data local portuguesa; o desempenho é independente das restantes oito estratégias.
- **Sem evento ou cotação elegível não é aberta uma posição falsa**. O campo `daily_reference` no relatório fica com `status=NO_ELIGIBLE_QUOTE` e `opened_today=0`.

## Interpretação

Esta nona carteira é **controlo**, não sinal de aposta com valor nem estratégia com vantagem. A sua seleção pode ter valor esperado negativo devido à margem da casa, comissão, limitação de preços ou outras condições. Deve ser analisada em separado do conjunto das oito estratégias quantitativas.

A meta de 1–3 eventos distintos por dia é um objetivo operacional *quando os dados permitem*, nunca justificação para fabricar odds, apostar em jogos já iniciados ou ignorar limites de exposição.

Painel: https://telmix-sudo.github.io/sports-intelligence-v3/strategies.html

Relatório verificável: https://github.com/telmix-sudo/sports-intelligence-v3/blob/main/reports/strategy_lab_latest.json

## Correções de funcionamento (28/10/2026)

- O scanner deve preservar os resultados mesmo quando outras execuções atualizam o repositório simultaneamente (retries de push com rebase e falha explícita se persistência impossível).
- Quando o GitHub atrasa a execução, recuperar as janelas horárias de odds ainda não consultadas nesse dia, mantendo o orçamento de 10 chamadas por dia.
- O campo `daily_activity` no relatório distingue o número de **jogos diferentes** do número de **operações repetidas** entre carteiras.
- O comparador apresenta 12 carteiras (8 estratégias quantitativas, uma referência diária e três experiências de frequência semanal). As três estratégias de frequência semanal conservam a sua definição; a meta diária de 1–3 jogos é medida no sistema global.
- Uma sequência sem entradas não significa necessariamente falha de software, mas deve surgir com diagnóstico de odds ausentes ou insuficientes.

## Meta individual das oito carteiras principais (30/10/2026)

Cada uma das oito estratégias quantitativas originais passa a ter uma meta operacional **individual de 1 a 3 novas posições PAPER por dia civil de Lisboa** e um teto efetivo de **3 entradas diárias por carteira**. O relatório `daily_activity.per_strategy` publica contagens, meta e cumprimento por carteira; `strategies_below_minimum` identifica carteiras abaixo da meta. As carteiras continuam independentes, com saldos, ledgers, modelos e histórico preservados. As três experiências de frequência e a carteira de referência mantêm regras próprias.

**A meta não é uma obrigação de apostar sem vantagem.** Continuam obrigatórios evento futuro, cotação genuína com timestamp, modelo aplicável, edge/EV mínimo, deduplicação e limites de exposição. Se uma carteira tiver zero oportunidades elegíveis, regista zero e sinaliza a falha da meta. A ampliação de cobertura e a calibração dos modelos são trabalho adicional, não uma autorização para afrouxar filtros. Não existem apostas reais.
