#!/usr/bin/env bash
# Teste de integração de um contentor já saudável; não revela credenciais.
set -euo pipefail
docker compose exec -T freqtrade python - <<'PY'
import json, subprocess, sys
snapshot=json.loads(subprocess.check_output([sys.executable, 'scripts/snapshot.py']))
config=snapshot['show_config']
assert config['dry_run'] is True, 'O contentor não está em dry-run.'
assert config['state'] == 'running', 'O motor não está ativo.'
assert config['stake_currency'] == 'EUR'
assert snapshot['balance']['starting_capital'] == 250
assert len(snapshot['whitelist']['whitelist']) == 8
print(json.dumps({'dry_run':True,'initial_capital':250,'currency':'EUR',
                  'state':config['state'],'pairs':8,
                  'open_positions':len(snapshot['status'])}))
PY
