# Freqtrade — simulação com 250 €

Pacote exclusivo de paper trading, Freqtrade 2026.10. Não contém chaves de negociação.
Os dados são públicos da Kraken; não é necessário ter conta nessa plataforma.

## Configuração

| Parâmetro | Valor |
| --- | --- |
| Capital inicial | 250 EUR fictícios |
| Operação | 30 EUR; máximo 3 posições simultâneas |
| Mercado | Spot, apenas compras, sem alavancagem |
| Pares | BTC, ETH, SOL, XRP, ADA, LTC, DOT e LINK contra EUR |
| Velas | 15 minutos |
| Estratégia | Tendência SMA20/50/200 e recuperação de RSI acima de 45 |
| Stop-loss | -3% de preço; custos e slippage podem aumentar a perda |
| Prazo máximo | 48 horas por posição; saída processada no ciclo seguinte |
| Comissões simuladas | 0,8% na entrada e 0,8% na saída |
| Spread máximo para entrada | 0,3%, verificado no livro atual |
| Proteções | Pausa por par; pausa global após 3 stops; proteção de drawdown |

A comissão de 0,8% por lado usa o escalão inicial spot/taker publicado pela Kraken
em 03/11/2026: https://www.kraken.com/features/fee-schedule.
Rever a tabela antes de mudar de plataforma ou simular outro escalão. Não reduzir
custos para obter artificialmente um backtest favorável.

Esta é uma estratégia baseline experimental. Não há rentabilidade demonstrada.
Não força entradas diárias. FreqAI não está ativo: primeiro é necessário obter
um benchmark e um histórico de simulação. O backtest OHLCV não reproduz o livro
de ordens histórico nem toda a derrapagem; o dry-run também não reproduz impacto
de mercado e execução real de forma exata.

## Iniciar num computador ou servidor com Docker

Na pasta deste pacote:

```bash
docker compose up -d --build
docker compose logs --tail=80
```

O motor arranca automaticamente em dry-run. O painel fica em http://localhost:8080.
Para consultar o utilizador e a palavra-passe gerada localmente:

```bash
docker compose exec freqtrade python -c 'import json; p=json.load(open("/freqtrade/runtime/access.json")); print("Utilizador:", p["username"]); print("Palavra-passe:", p["password"])'
```

Mantenha esses dados privados. A porta é publicada apenas no próprio computador.
Para acesso remoto, usar um túnel SSH ou HTTPS com autenticação. O pacote não
abre uma porta pública por defeito.

```bash
docker compose exec freqtrade python scripts/healthcheck.py
docker compose exec freqtrade python scripts/snapshot.py
docker compose exec freqtrade python scripts/backup.py
docker compose stop
docker compose up -d
```

As posições e os resultados são preservados no volume `paper-state`. Não executar
`docker compose down -v`: apaga o volume e o histórico. Os backups manuais ficam
nesse mesmo volume; copiar para outro suporte para proteção contra falha do disco.
O supervisor reinicia o motor se terminar ou após três verificações de saúde
consecutivas sem sucesso, depois de três minutos de arranque. Confirma o ciclo
do motor e a atualidade dos dados dos oito pares. Não pode corrigir uma falha
prolongada da plataforma ou da rede; continua a tentar respeitando uma pausa.
Guarda também um backup de SQLite a cada seis horas, conservando os últimos sete.

## Instalação nativa alternativa

Requer Python 3.12+:

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/freqtrade install-ui
PATH="$PWD/.venv/bin:$PATH" .venv/bin/python scripts/supervise.py
```

O processo tem de continuar em execução; fechar o terminal pode pará-lo. Para
24/7 usar Docker com reinício automático num servidor, ou um gestor de serviços.
O histórico nativo fica em `runtime/`.

## Testes e backtest

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/verify_runtime.py
.venv/bin/python scripts/download_recent.py
.venv/bin/python scripts/start.py --prepare-only
.venv/bin/freqtrade backtesting --config runtime/config.runtime.json --strategy PaperTrend250 --enable-protections --export trades --cache none
```

A API OHLC da Kraken limita a janela recente a cerca de 720 velas. O script acima só serve
para um teste operacional curto. Para validar desempenho, importar um histórico
mais longo, separar períodos de desenvolvimento e teste e comparar com comprar
e manter, após custos. Não confundir esse teste curto com validação da estratégia.

## Alojamento

O ambiente desta conversa é temporário. Uma execução iniciada aqui não constitui
um serviço 24/7. O plano gratuito do Render adormece após inatividade e não conserva
SQLite local entre reinícios. O alojamento contínuo exige um computador/servidor
sempre ligado e armazenamento persistente. Este pacote não subscreve serviços pagos.

Em alojamento com Docker, montar um disco persistente em `/freqtrade/runtime`,
usar apenas uma instância e configurar `PAPER_BIND=0.0.0.0`, `PORT` e uma
`PAPER_UI_PASSWORD` privada com pelo menos 16 caracteres. A base de dados não pode
estar no sistema de ficheiros efémero. Verificar o ciclo com `healthcheck.py` e
o estado `running` do motor após cada implantação.

### Configuração Render preparada

`render.yaml` define um novo serviço isolado com 512 MB de RAM e disco persistente
de 1 GB. Preço publicado em 03/11/2026: 7 USD/mês de computação + 0,25 USD/mês
de disco, antes de impostos e eventual utilização adicional. Não foi criado nem
ativado nenhum serviço pago. O plano pequeno deve ser monitorizado por consumo
de memória; qualquer aumento de plano exige rever o custo.

A configuração passa o JSON Schema público do Render; o CLI do Render não está
instalado neste ambiente. A imagem Docker foi confirmada no registo oficial, mas
não foi construída localmente porque Docker não está disponível. Os testes
integrados foram executados numa instalação nativa do Freqtrade.

A implantação deve usar a branch `freqtrade-paper-250-eur` e o ficheiro
`bots/freqtrade-paper/render.yaml`. A palavra-passe do painel é gerada pelo Render
e fica nas variáveis privadas do serviço. O supervisor verifica dados e ciclo;
a rota `/api/v1/ping` usada pelo alojamento confirma apenas que a API responde.

### Alojamento gratuito escolhido

A instalação preparada para a **Oracle Cloud Always Free** está em
[deploy/oracle/README.md](deploy/oracle/README.md). Usar essa via antes de
considerar o Render pago. A execução na Oracle ainda depende de acesso à conta,
disponibilidade de recursos elegíveis e validação final do servidor.
