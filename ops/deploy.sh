#!/usr/bin/env bash
# Sobe o gatilho: Secret Manager -> Cloud Function -> Cloud Scheduler.
#
# Rode do diretorio raiz do repositorio. E idempotente: pode rodar de novo
# depois de mudar o main.py que ele so atualiza o que precisa.
#
# Antes de rodar, tenha em maos um PAT fine-grained do GitHub com:
#   Repository access: apenas leagostini/onlytennis-feed
#   Permissions: Actions = Read and write   (so isso; nada de contents)
# Esse e o escopo minimo que o workflow_dispatch exige.
set -euo pipefail

PROJECT="${PROJECT:?defina PROJECT com o id do projeto GCP}"
REGION="${REGION:-us-central1}"
REPO="${REPO:-leagostini/onlytennis-feed}"
SEGREDO="${SEGREDO:-onlytennis-feed-pat}"
FUNCAO="${FUNCAO:-feed-dispatcher}"
JOB="${JOB:-feed-15min}"
CRON="${CRON:-*/15 * * * *}"

SA_FUNCAO="${FUNCAO}@${PROJECT}.iam.gserviceaccount.com"
SA_SCHEDULER="${JOB}-invoker@${PROJECT}.iam.gserviceaccount.com"

# O projeto e a conta valem so para este processo. Nada de `gcloud config set`:
# a maquina tem tres contas autenticadas e o quota project global aponta para
# outro projeto, entao mexer na config global e o caminho curto para rodar no
# lugar errado depois.
export CLOUDSDK_CORE_PROJECT="$PROJECT"
export CLOUDSDK_BILLING_QUOTA_PROJECT="$PROJECT"
if [ -n "${CONTA:-}" ]; then
  export CLOUDSDK_CORE_ACCOUNT="$CONTA"
fi

echo "==> habilitando APIs"
gcloud services enable \
  cloudfunctions.googleapis.com run.googleapis.com cloudbuild.googleapis.com \
  cloudscheduler.googleapis.com secretmanager.googleapis.com

echo "==> segredo"
if ! gcloud secrets describe "$SEGREDO" >/dev/null 2>&1; then
  gcloud secrets create "$SEGREDO" --replication-policy=automatic
fi
# Le o PAT sem deixar rastro no historico do shell nem em arquivo.
if [ "${GRAVAR_PAT:-1}" = "1" ]; then
  printf 'Cole o PAT fine-grained do GitHub (a digitacao fica oculta): '
  read -rs PAT; echo
  printf '%s' "$PAT" | gcloud secrets versions add "$SEGREDO" --data-file=-
  unset PAT
fi

echo "==> contas de servico"
# Duas identidades separadas de proposito: a funcao le o segredo mas nao pode
# se invocar; o scheduler invoca a funcao mas nunca ve o segredo.
gcloud iam service-accounts create "$FUNCAO" \
  --display-name="dispara o build-feed" 2>/dev/null || true
gcloud iam service-accounts create "${JOB}-invoker" \
  --display-name="invoca a funcao do feed" 2>/dev/null || true

gcloud secrets add-iam-policy-binding "$SEGREDO" \
  --member="serviceAccount:${SA_FUNCAO}" \
  --role=roles/secretmanager.secretAccessor >/dev/null

echo "==> funcao"
gcloud functions deploy "$FUNCAO" \
  --gen2 \
  --runtime=python312 \
  --region="$REGION" \
  --source=ops/dispatcher \
  --entry-point=disparar \
  --trigger-http \
  --no-allow-unauthenticated \
  --service-account="$SA_FUNCAO" \
  --set-secrets="GITHUB_TOKEN=${SEGREDO}:latest" \
  --set-env-vars="GITHUB_REPO=${REPO},GITHUB_WORKFLOW=feed.yml,GITHUB_REF=master" \
  --memory=256Mi \
  --timeout=60s \
  --max-instances=3

URL="$(gcloud functions describe "$FUNCAO" --region="$REGION" --format='value(serviceConfig.uri)')"
echo "==> funcao em $URL"

gcloud functions add-invoker-policy-binding "$FUNCAO" --region="$REGION" \
  --member="serviceAccount:${SA_SCHEDULER}" >/dev/null

echo "==> job do scheduler"
CRIAR=create
gcloud scheduler jobs describe "$JOB" --location="$REGION" >/dev/null 2>&1 && CRIAR=update
gcloud scheduler jobs "$CRIAR" http "$JOB" \
  --location="$REGION" \
  --schedule="$CRON" \
  --time-zone=UTC \
  --uri="$URL" \
  --http-method=POST \
  --oidc-service-account-email="$SA_SCHEDULER" \
  --oidc-token-audience="$URL" \
  --attempt-deadline=60s

echo
echo "pronto. teste agora com:"
echo "  gcloud scheduler jobs run $JOB --location=$REGION"
echo "e confira a execucao nova em:"
echo "  https://github.com/${REPO}/actions/workflows/feed.yml"
