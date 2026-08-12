#!/usr/bin/env bash
# Liga o gatilho novo do robô do Circuito. Um comando só, do começo ao fim.
#
# Pede o token do GitHub (a digitação fica oculta), confere se ele serve
# ANTES de gravar, guarda no Secret Manager, publica a versão nova da função,
# solta o agendador e dispara uma execução de teste. No fim diz, em bom
# português, se funcionou.
#
# Rode do diretório do repositório:  ./ops/ligar.sh
set -euo pipefail

PROJECT="${PROJECT:-only-tennis}"
REGION="${REGION:-us-central1}"
CONTA="${CONTA:-leandro.contact@gmail.com}"
REPO="${REPO:-leagostini/onlytennis-feed}"
SEGREDO="${SEGREDO:-onlytennis-feed-pat}"
FUNCAO="${FUNCAO:-feed-dispatcher}"
JOB="${JOB:-feed-15min}"

# Conta e projeto valem só para este processo: a máquina tem três contas
# autenticadas e o quota project global aponta para outro projeto.
export CLOUDSDK_CORE_ACCOUNT="$CONTA"
export CLOUDSDK_CORE_PROJECT="$PROJECT"
export CLOUDSDK_BILLING_QUOTA_PROJECT="$PROJECT"

if [ ! -d ops/dispatcher ]; then
  echo "ERRO: rode este script de dentro da pasta do repositório onlytennis-feed."
  echo "      cd ~/dev/onlytennis-feed && ./ops/ligar.sh"
  exit 1
fi

echo
echo "=== Passo 1 de 5: o token ==="
printf 'Cole o token do GitHub e aperte Enter (não aparece nada na tela, é normal): '
read -rs PAT
echo
if [ -z "$PAT" ]; then
  echo "ERRO: nada foi colado. Rode de novo."
  exit 1
fi

echo
echo "=== Passo 2 de 5: conferindo se o token serve ==="
# 200 aqui significa que o token enxerga o robô neste repositório. A permissão
# de escrita só o disparo de verdade comprova, e é o passo 5.
CODIGO="$(curl -s -o /dev/null -w '%{http_code}' \
  -H "Authorization: Bearer ${PAT}" \
  -H "Accept: application/vnd.github+json" \
  "https://api.github.com/repos/${REPO}/actions/workflows/feed.yml")"

if [ "$CODIGO" != "200" ]; then
  echo "ERRO: o GitHub respondeu ${CODIGO} e o token não foi gravado."
  case "$CODIGO" in
    401) echo "      401 = token inválido ou copiado pela metade. Gere outro e tente de novo." ;;
    403) echo "      403 = falta a permissão Actions no token." ;;
    404) echo "      404 = o token não tem acesso ao repositório ${REPO}." ;;
    *)   echo "      Sem internet ou GitHub fora do ar. Tente daqui a pouco." ;;
  esac
  unset PAT
  exit 1
fi
echo "ok, o token enxerga o robô."

echo
echo "=== Passo 3 de 5: guardando o token no cofre ==="
printf '%s' "$PAT" | gcloud secrets versions add "$SEGREDO" --data-file=- >/dev/null
unset PAT
echo "ok, guardado no Secret Manager (ninguém precisa vê-lo de novo)."

echo
echo "=== Passo 4 de 5: publicando a função (leva uns 2 minutos) ==="
# O token entra na função como variável de ambiente, resolvida quando a
# instância sobe: sem uma revisão nova, a que está de pé seguiria com o
# valor antigo.
gcloud functions deploy "$FUNCAO" \
  --gen2 --runtime=python312 --region="$REGION" \
  --source=ops/dispatcher --entry-point=disparar \
  --trigger-http --no-allow-unauthenticated \
  --service-account="${FUNCAO}@${PROJECT}.iam.gserviceaccount.com" \
  --set-secrets="GITHUB_TOKEN=${SEGREDO}:latest" \
  --set-env-vars="GITHUB_REPO=${REPO},GITHUB_WORKFLOW=feed.yml,GITHUB_REF=master" \
  --memory=256Mi --timeout=60s --max-instances=3 \
  --quiet >/dev/null
echo "ok, função no ar."

echo
echo "=== Passo 5 de 5: ligando o agendador e testando ==="
ANTES="$(curl -s "https://api.github.com/repos/${REPO}/actions/workflows/feed.yml/runs?event=workflow_dispatch&per_page=1" \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); r=d.get("workflow_runs") or [{}]; print(r[0].get("id",0))')"

gcloud scheduler jobs resume "$JOB" --location="$REGION" >/dev/null
gcloud scheduler jobs run "$JOB" --location="$REGION" >/dev/null
echo "agendador ligado, disparo de teste enviado. Esperando o GitHub responder..."

DEPOIS="$ANTES"
for _ in $(seq 1 12); do
  sleep 5
  DEPOIS="$(curl -s "https://api.github.com/repos/${REPO}/actions/workflows/feed.yml/runs?event=workflow_dispatch&per_page=1" \
    | python3 -c 'import json,sys; d=json.load(sys.stdin); r=d.get("workflow_runs") or [{}]; print(r[0].get("id",0))')"
  [ "$DEPOIS" != "$ANTES" ] && break
done

echo
if [ "$DEPOIS" != "$ANTES" ]; then
  echo "FUNCIONOU."
  echo "O robô acabou de rodar por ordem do agendador novo, e a partir de agora"
  echo "isso se repete de 15 em 15 minutos, sem depender do cron do GitHub."
  echo "Acompanhe em: https://github.com/${REPO}/actions/workflows/feed.yml"
else
  echo "ATENÇÃO: o agendador foi ligado, mas o disparo de teste ainda não apareceu."
  echo "Pode ser só demora do GitHub. Veja a lista de execuções em:"
  echo "  https://github.com/${REPO}/actions/workflows/feed.yml"
  echo "Se em 2 minutos não aparecer nada novo, me chame e eu leio o log:"
  echo "  gcloud functions logs read ${FUNCAO} --region=${REGION} --project=${PROJECT} --limit=5"
fi
echo
