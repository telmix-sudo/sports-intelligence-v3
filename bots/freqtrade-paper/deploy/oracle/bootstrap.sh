#!/usr/bin/env bash
# Instala o bot numa VM Ubuntu já criada. Não cria recursos nem subscreve serviços.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

if [[ ${EUID} -ne 0 ]]; then
  echo 'Executar como administrador no servidor Oracle.' >&2
  exit 1
fi
. /etc/os-release
if [[ ${ID} != ubuntu || ${VERSION_ID} != 24.04 ]]; then
  echo 'Este instalador requer uma VM nova com Ubuntu 24.04 LTS.' >&2
  exit 1
fi
case "$(dpkg --print-architecture)" in
  arm64|amd64) ;;
  *) echo 'Arquitetura não suportada.' >&2; exit 1 ;;
esac

apt-get update
apt-get install -y ca-certificates curl git
if ! command -v docker >/dev/null 2>&1; then
  install -m 0755 -d /etc/apt/keyrings
  curl --fail --silent --show-error --location --retry 3 \
    https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  docker_arch=$(dpkg --print-architecture)
  cat > /etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: ${UBUNTU_CODENAME:-$VERSION_CODENAME}
Components: stable
Architectures: ${docker_arch}
Signed-By: /etc/apt/keyrings/docker.asc
EOF
  apt-get update
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi
docker compose version
systemctl enable --now docker

repo_url=https://github.com/telmix-sudo/sports-intelligence-v3.git
source_ref=${PAPER_SOURCE_REF:-freqtrade-paper-250-eur}
if [[ ! ${source_ref} =~ ^[A-Za-z0-9][A-Za-z0-9._/-]*$ ]]; then
  echo 'Referência de código inválida.' >&2
  exit 1
fi
source_dir=/opt/freqtrade-paper/source
install -m 0755 -d /opt/freqtrade-paper
if [[ ! -d ${source_dir}/.git ]]; then
  git init "${source_dir}"
  git -C "${source_dir}" remote add origin "${repo_url}"
fi
if [[ $(git -C "${source_dir}" remote get-url origin) != "${repo_url}" ]]; then
  echo 'A pasta de instalação pertence a outro repositório.' >&2
  exit 1
fi
git -C "${source_dir}" diff --exit-code
git -C "${source_dir}" diff --cached --exit-code
git -C "${source_dir}" fetch --depth 1 origin "${source_ref}"
git -C "${source_dir}" checkout --detach FETCH_HEAD
app_dir=${source_dir}/bots/freqtrade-paper
cd "${app_dir}"

# O nome do projeto mantém o mesmo volume entre reinícios e atualizações.
docker compose --project-name freqtrade-paper config --quiet
docker compose --project-name freqtrade-paper up -d --build --wait --wait-timeout 360
docker compose --project-name freqtrade-paper exec -T freqtrade python scripts/healthcheck.py
git -C "${source_dir}" rev-parse HEAD > /opt/freqtrade-paper/deployed-commit.txt

echo 'Bot instalado: carteira inicial de 250 EUR, exclusivamente dry-run.'
echo 'O histórico fica num volume persistente do Docker. Não executar down -v.'
echo 'O painel está ligado apenas ao endereço local do servidor: 127.0.0.1:8080.'
echo 'O acesso remoto com HTTPS deve ser configurado antes de publicar o painel.'
