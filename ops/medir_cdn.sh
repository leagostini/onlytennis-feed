#!/usr/bin/env bash
# Mede quanto tempo a borda do GitHub Pages serve conteudo velho depois de um
# deploy. E isso que decide se o app precisa de cache-busting ou se basta
# revalidacao forcada.
#
# Por que nao da para testar com If-None-Match: se a borda tem copia fresca do
# objeto ANTIGO e voce manda o ETag ANTIGO, ela compara com o que ela tem,
# casa, e devolve 304 sem falar com a origem. O 304 e o sintoma do caso ruim,
# nao a prova de que esta tudo bem. Por isso o teste usa uma URL com parametro
# unico como controle: ela e miss garantido e le a verdade da origem.
#
# PRE-CONDICAO, e a parte que mais invalida teste: a execucao precisa ter
# publicado de verdade. O workflow so faz deploy quando o conteudo muda, e
# rodar durante jogo ao vivo nao garante mudanca -- o payload carrega games
# por set, entao uma partida passa fácil de 40 min sem alterar um byte.
# A validacao e olhar a run: precisa ter os DOIS jobs, `build` e `deploy`.
# So `build` = deploy dispensado = teste invalido, descarte e repita.
#
# Repita em pelo menos tres publicacoes antes de decidir: o Fastly espalha as
# requisicoes entre nos, entao uma coleta so nao representa a CDN inteira.
set -uo pipefail

URL="${URL:-https://leagostini.github.io/onlytennis-feed/latest.json}"
POLLS="${POLLS:-48}"
INTERVALO="${INTERVALO:-15}"
HN=/tmp/otcdn_n.h
HR=/tmp/otcdn_r.h

gen() { python3 -c 'import json,sys;print(json.load(sys.stdin).get("generatedAt","?"))' 2>/dev/null || printf '?'; }
hdr() { grep -i "^$2:" "$1" 2>/dev/null | tr -d '\r' | cut -d' ' -f2- | head -1; }

echo "== 1/3 aquecendo a borda =="
# O aquecimento tem de ser perto do deploy. Se for muito antes, a entrada
# vence sozinha (max-age=600), a borda rebusca naturalmente e isso parece
# purga. Aqueca e dispare em seguida.
curl -s --compressed -o /tmp/otcdn_w.json -D /tmp/otcdn_w.h "$URL"
printf '   generatedAt=%s  etag=%s  age=[%s]\n' \
  "$(gen < /tmp/otcdn_w.json)" "$(hdr /tmp/otcdn_w.h etag)" "$(hdr /tmp/otcdn_w.h age)"

echo
echo "== 2/3 dispare o workflow agora =="
echo "   gcloud scheduler jobs run feed-15min --location=us-central1"
echo "   (ou o botao Run workflow em Actions)"
echo
echo "   Espere a run terminar e CONFIRA: ela tem os jobs build E deploy?"
echo "   Se so tiver build, o deploy foi dispensado -- aborte e repita depois."
printf '   Deploy verde? [enter para coletar, ctrl-c para abortar] '
read -r _

echo
echo "== 3/3 coletando por $(( POLLS * INTERVALO / 60 )) min =="
echo "   norm = URL normal (a que o app usa)   ref = URL unica (verdade da origem)"
T0=$(date +%s)
for i in $(seq 1 "$POLLS"); do
  # $(date +%s%N) NAO serve: o date do BSD/macOS nao tem %N e devolve o
  # literal "N", a URL vira constante e o controle passa a ser cacheado.
  curl -s --compressed -o /tmp/otcdn_n.json -D "$HN" "$URL"
  curl -s --compressed -o /tmp/otcdn_r.json -D "$HR" "${URL}?probe=${T0}-${i}"
  N=$(gen < /tmp/otcdn_n.json); R=$(gen < /tmp/otcdn_r.json)
  [ "$N" = "$R" ] && VEREDITO=igual || VEREDITO=ANTIGO
  printf '%s  norm=%s  ref=%s  %-6s age=[%s] cache=%s node=%s\n' \
    "$(date +%H:%M:%S)" "$N" "$R" "$VEREDITO" \
    "$(hdr "$HN" age)" "$(hdr "$HN" x-cache)" "$(hdr "$HN" x-served-by)"
  sleep "$INTERVALO"
done

cat <<'FIM'

== como ler ==
Nao espere um degrau limpo. O Fastly atende requisicoes da mesma URL a partir
de nos diferentes, entao as respostas alternam entre antigo e novo. O numero
que interessa e a PROPORCAO de linhas ANTIGO ao longo do tempo, e quando ela
zera.

  linhas ANTIGO por varios minutos  -> a borda nao e purgada no deploy.
                                       o app precisa de cache-busting.
  tudo "igual" desde a primeira     -> a borda e purgada no deploy.
                                       basta .reloadRevalidatingCacheData.

Registre o node, mas nao exija que ele se repita: URLs diferentes percorrem
nos diferentes por roteamento, nao por falha do teste.
FIM
