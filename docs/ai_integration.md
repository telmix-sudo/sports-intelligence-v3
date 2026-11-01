# Integração das ferramentas de IA

## Operação

- `requirements-ml.txt` fixa as versões principais testadas de Optuna/LightGBM.
  O fluxo diário ML tem instalação própria, dois threads, até 12 experiências e
  limite de 180s para a procura. O scanner e as carteiras horárias continuam
  independentes destas dependências.
- O fluxo `ml-lab.yml` executa às 05:07 UTC, manualmente ou quando muda o código ML.
  Recolhe resultados OpenFootball, separa datas de treino/afinação/calibração e
  avaliação final, compara com Elo e Poisson e publica `reports/ml_lab_latest.json`.
- Modelos finais treinados com todo o histórico servem apenas previsões futuras.
  Não são usados para calcular o resultado da avaliação histórica. A repetição
  diária sobre o mesmo período não cria uma amostra independente nova.
- As previsões são congeladas em `data/ml_observations_v3.jsonl`, exclusivamente
  com stake zero. Só resultados finais oficiais geram liquidações de scoring.
  Odds completas e com timestamp podem servir de comparação prospetiva.
- Sem histórico de odds com hora válida, ROI histórico e lucro ficam indisponíveis.
  O novo laboratório não cria carteiras nem altera as existentes.

## CodeRabbit e Copilot

`.coderabbit.yaml`, `.github/copilot-instructions.md` e `AGENTS.md` definem regras
para contabilidade, timestamps, quotas, mercados, dados permitidos e avaliação
cronológica. Estes ficheiros não instalam aplicações nem comprovam acesso a planos.

CodeRabbit precisa da instalação GitHub autorizada para este repositório. O
Copilot precisa de acesso ativo na conta; o benefício GitHub Education exige
validação pelo próprio GitHub. Nenhuma subscrição paga é ativada por código.

O inventário `reports/ai_review_latest.json` regista atividade dos bots observada
nas 20 propostas de alteração mais recentemente atualizadas. Não armazena o texto
das revisões nem executa instruções contidas nos comentários. Ausência de atividade
não prova ausência de instalação. O acompanhamento e as correções concretas são
feitos através do GitHub; instalar um revisor não cria uma sessão de chat permanente.

## Verificação

```bash
python -m pip install -r requirements-ml.txt
PYTHONPATH=src python -m unittest discover -s tests -v
PYTHONPATH=src python -m sports_intelligence.ml_lab --trials 12 --timeout 180
```

[Painel ML](ml.html) · [CodeRabbit](https://github.com/apps/coderabbitai) ·
[GitHub Education](https://github.com/settings/education/benefits)
