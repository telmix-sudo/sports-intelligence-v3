# Verificação de 3 de novembro de 2026

## Funcionamento

- Freqtrade 2026.10 instalado e executado nativamente com Python 3.12.14.
- Carteira inicial confirmada pela API: 250 EUR fictícios.
- Oito pares EUR com dados reais atuais da Kraken.
- Estado `running`, exclusivamente `dry_run`; nenhuma chave de negociação.
- Painel web responde; a API de saldo recusa pedidos sem autenticação.
- Reinício preserva a conta e as credenciais; backup de SQLite efetuado em execução.
- Seis testes unitários aprovados: schema, bloqueio de dinheiro real/overrides,
  preservação de estado, causalidade dos sinais, spread e saída por prazo.
- Configuração Render validada com o JSON Schema público atual.
- O contentor Docker foi construído e executado com sucesso em Intel/AMD64 e ARM64
  através de testes no GitHub. Foram confirmados o modo dry-run, os 250 EUR, os
  oito pares atuais e a preservação da carteira após reinício do contentor.
- Evidência: https://github.com/telmix-sudo/sports-intelligence-v3/actions/runs/41632913260

## Backtest operacional curto

Período UTC: 28/10/2026 17:15 a 03/11/2026 00:15. Após aquecimento dos indicadores,
foram testados cerca de cinco dias. Dados públicos reais, velas fechadas e comissão
simulada de 0,8% por lado. Proteções ativadas. Sem livro de ordens histórico.

| Indicador | Resultado |
| --- | --- |
| Capital inicial do backtest | 250,00 EUR |
| Capital final do backtest | 243,98 EUR |
| Resultado líquido | -6,02 EUR (-2,41%) |
| Operações | 10 |
| Ganhos / perdas | 0 / 10 |

O resultado é negativo. A amostra é pequena e não valida rentabilidade. Não foi
feita otimização para melhorar este período. Não atribuir estes resultados a uma
carteira que já esteja a funcionar 24/7: são exclusivamente do teste histórico.
A configuração da nova simulação começa com 250 EUR.

## Estado de implantação

Os processos usados na verificação foram parados no final do teste. Não há serviço
24/7 nem alojamento pago ativado. A via escolhida passou a ser a Oracle Cloud
Always Free. Estão preparados um instalador Ubuntu 24.04 e o respetivo cloud-init,
com arranque automático e volume persistente. O instalador não cria recursos
Oracle; só é executado depois de aprovisionar uma máquina gratuita elegível.

O navegador disponível devolveu "Site Unavailable" ao abrir https://cloud.oracle.com/,
incluindo após uma nova tentativa. Ainda não foi possível aceder a uma conta,
verificar quotas/vagas ou criar o servidor. O acesso remoto por HTTPS ao painel
também só será configurado depois de o servidor existir. Não apresentar o bot
como ativo enquanto estes passos e a verificação no servidor não estiverem feitos.

## Referências

- https://www.freqtrade.io/en/stable/
- https://www.kraken.com/features/fee-schedule
- https://render.com/docs/free
- https://render.com/pricing
- https://render.com/schema/render.yaml.json
