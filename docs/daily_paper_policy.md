# Meta de atividade diária — V3

**Introduzida em 27/10/2026, hora de Portugal.**

## Objetivo

Produzir pelo menos uma **posição fictícia nova por dia civil de Portugal** sempre que haja um evento futuro com cotação genuína, completa, atual e verificável. Não confundir frequência de testes com evidência de valor.

Os oito modelos anteriores continuam sob as suas próprias regras de edge e EV, sem entrada forçada. Para medir o que acontece quando se tenta manter atividade diária, há uma nona carteira totalmente separada denominada `MARKET_DAILY_REFERENCE`.

## Market Daily Reference

- Nova banca **1.000 € fictícios**. Os saldos de todas as outras carteiras ficam intactos.
- Seleção de referência: **favorito do mercado** pelo consenso das probabilidades *no-vig* de pelo menos duas casas. Não afirma que o favorito tem valor esperado positivo.
- Modalidades: futebol europeu (mercado 1X2), NFL, NBA, NHL e MLB (mercado moneyline).
- Apenas jogos ainda não iniciados e até 48h do início; odds reais da The Odds API com timestamp da própria cotação de, no máximo, 6 horas.
- Uma entrada de referência por dia civil de Lisboa, sem repetir um evento já selecionado anteriormente.
- Stake: 0,5% da banca fictícia da própria carteira, exposição em aberto limitada a 2%; se o limite estiver atingido, a entrada é rejeitada.
- Em dinheiro real não existe qualquer ligação a corretora ou casa de apostas e nenhuma operação é executada.
- Depois do jogo, resultado oficial confirma a liquidação: vitória gera lucro fictício segundo a odd registada, derrota perde a stake, empate em moneyline de dois resultados é considerado anulado (VOID) sem prejuízo fictício.
- Guarda hora de previsão, event ID, casa, mercado, preço, probabilidade, stake e origem. Contagens por dia são calculadas pela data local portuguesa; o desempenho é independente das restantes oito estratégias.
- **Sem evento ou cotação elegível não é aberta uma posição falsa**. O campo `daily_reference` no relatório fica com `status=NO_ELIGIBLE_QUOTE` e `opened_today=0`.

## Interpretação

Esta nona carteira é **controlo**, não sinal de aposta com valor nem estratégia com vantagem. A sua seleção pode ter valor esperado negativo devido à margem da casa, comissão, limitação de preços ou outras condições. Deve ser analisada em separado do conjunto das oito estratégias quantitativas.

A meta de um por dia é um objetivo operacional *quando os dados permitem*, nunca justificação para fabricar odds, apostar em jogos já iniciados ou ignorar limites de exposição.

Painel: https://telmix-sudo.github.io/sports-intelligence-v3/strategies.html

Relatório verificável: https://github.com/telmix-sudo/sports-intelligence-v3/blob/main/reports/strategy_lab_latest.json
