# Teste PAPER de futebol em jogo

O laboratório regista a primeira captura elegível por jogo, entre 25 e 75
minutos nas cinco ligas principais já modeladas. Usa o marcador, relógio e IDs
do calendário ESPN e confirma o estado e os IDs no resumo. A captura do
calendário tem de anteceder a do resumo em até 120 segundos. Exige um evento
recente com relógio verificável e contagens explícitas de remates enquadrados e
expulsões das duas equipas. Campos em falta, interrupções e jogos terminados
ficam fora da amostra.

- **Base.** Aplica as taxas Poisson da V3, ajustadas ao tempo regulamentar
  restante, ao marcador observado. As taxas usam apenas partidas anteriores ao
  dia do jogo.
- **Contexto.** Usa as mesmas taxas com um ajuste experimental fixo pelo
  diferencial de remates enquadrados e de expulsões. Os remates são contagens.
  As taxas de golos e os ajustes são estimativas.
- **Comparação.** 1X2 e mais/menos de 2,5 golos enquanto a linha estiver em aberto.
  As duas previsões ficam fixadas em `data/in_play_v3.jsonl`. O resultado
  regulamentar confirmado é registado separadamente. O relatório calcula taxa
  de acerto, calibração da opção mais provável, Brier e Log Loss por liga e
  mercado. A partir de 100 jogos em 14 datas por grupo, estima intervalos de 95%
  para as diferenças emparelhadas por reamostragem de datas.
- **Preços.** A recolha usa o horário existente do laboratório e acrescenta até
  12 consultas de resumos ESPN por execução. A quota de odds mantém-se. O piloto
  requer odds ao vivo sincronizadas para comparar com o mercado e calcular ROI.
  Funciona a 0 € e as previsões ficam fora das regras de entrada das carteiras.
