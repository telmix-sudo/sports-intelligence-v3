# Instalação na Oracle Cloud Always Free

Esta é a via de alojamento escolhida para o teste. Nenhum recurso Oracle é criado
por estes ficheiros. A conta, os recursos gratuitos disponíveis e a região ainda
têm de ser confirmados na consola. O bot só está ativo quando o servidor for
criado, a instalação terminar e os dados atuais forem verificados.

O contentor passou os testes reais de arranque e reinício em AMD64 e ARM64:
https://github.com/telmix-sudo/sports-intelligence-v3/actions/runs/41632913260
A instalação do sistema numa VM Oracle permanece pendente de acesso à conta.

## Configuração prevista

| Campo | Valor |
| --- | --- |
| Região | A região principal da conta Oracle |
| Máquina | VM.Standard.A1.Flex, com elegibilidade Always Free confirmada |
| CPU | 1 OCPU |
| Memória | 2 GB |
| Imagem | Ubuntu 24.04 LTS ARM64, fornecida pela Canonical e elegível como gratuita |
| Disco de arranque | 50 GB; dentro da quota gratuita total da conta |
| Carteira | 250 EUR fictícios |
| Histórico | Volume persistente do Docker; nunca apagado pelo instalador |
| Arranque | Automático, com supervisor e reinício do Docker |
| Chaves de negociação | Nenhuma |

A Oracle publica atualmente uma quota A1 gratuita equivalente a 2 OCPUs e 12 GB
de memória, além de 200 GB de armazenamento agregado na região principal. Não
assumir que a quota toda está disponível: contar primeiro os recursos já existentes.
Usar somente recursos elegíveis Always Free; os créditos temporários do período
experimental não transformam recursos pagos em alojamento permanente gratuito.
Não atualizar a conta para Pay As You Go para contornar uma falta de capacidade.

## Instalação automática ao criar a máquina

O ficheiro `cloud-init.yaml` é colocado no campo de inicialização da instância.
Instala Docker pelo repositório oficial e arranca o contentor do bot. O processo
pode demorar alguns minutos e a receção de dados da Kraken tem de ser confirmada.
Não cria regras de rede, não abre o painel publicamente e não pede palavras-passe.

O código é obtido da branch isolada `freqtrade-paper-250-eur`. A revisão instalada
fica registada em `/opt/freqtrade-paper/deployed-commit.txt`. Reexecutar o instalador
atualiza o código e conserva o volume com a carteira; não reinicia o saldo.

## Verificação no servidor

```bash
sudo cloud-init status --wait
cd /opt/freqtrade-paper/source/bots/freqtrade-paper
sudo docker compose --project-name freqtrade-paper ps
sudo docker compose --project-name freqtrade-paper exec -T freqtrade python scripts/healthcheck.py
```

O resultado deve confirmar um ciclo recente e os oito pares atualizados.
Consultar o estado financeiro com `scripts/snapshot.py`, sem publicar ficheiros de
credenciais. Os backups locais automáticos preservam os sete mais recentes no
mesmo volume; proteger o disco de arranque e configurar também cópias externas.

## Acesso ao painel

O painel fica inicialmente em `127.0.0.1:8080` no servidor. A instalação de um
acesso remoto por HTTPS é uma etapa adicional, depois de conhecer o endereço do
servidor. Nunca publicar a porta 8080 diretamente por HTTP. Não depender de um
computador pessoal para manter o motor em execução: o computador/telemóvel só
serve para consultar o painel ou a consola Oracle.

## Limitações

- A disponibilidade de uma máquina gratuita depende da capacidade da região.
- A Oracle pode recuperar instâncias Always Free consideradas inativas.
- A criação/verificação da conta e a aceitação dos respetivos termos pertencem
  ao titular. Não partilhar palavras-passe, dados de cartão ou códigos no chat.
- O teste histórico inicial foi negativo e não demonstra rentabilidade.

Referências oficiais verificadas em 03/11/2026:

- https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm
- https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier.htm
- https://docs.docker.com/engine/install/ubuntu/
