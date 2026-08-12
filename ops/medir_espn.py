#!/usr/bin/env python3
"""Mede a latencia da fonte e a granularidade de mudanca do feed.

Duas perguntas em aberto que so a fonte responde, e as duas saem da mesma
coleta:

1. Quanto a ESPN demora para marcar um jogo como encerrado depois do ultimo
   ponto. E o que sobra do orcamento de latencia: com agendador de 15 min, o
   erro de amostragem sozinho ja e 0-15 min, e a meta de 30 min so fecha se a
   fonte for rapida. O proxy medido aqui e o intervalo entre a ultima mudanca
   de placar e a virada de status para encerrado.

2. Com que frequencia o payload muda durante jogo. O contrato nao tem placar
   ao vivo, o que vem sao games por set -- entao "rodar durante jogo ao vivo"
   pode nao produzir mudanca nenhuma, e e isso que invalida o teste de CDN.
   Aqui da para ver quanto tempo o arquivo fica realmente parado com quadra
   cheia.

Uso: rode num dia de torneio, algumas horas, e interrompa com ctrl-c.

    python3 ops/medir_espn.py                 # coleta de 60 em 60s
    python3 ops/medir_espn.py --intervalo 30  # mais fino
    python3 ops/medir_espn.py --saida /tmp/espn.jsonl

O resumo sai na interrupcao; o JSONL fica para analise depois.
"""
import argparse
import json
import signal
import statistics
import sys
import urllib.request
from datetime import datetime, timezone

TOURS = ["atp", "wta"]
URL = "https://site.api.espn.com/apis/site/v2/sports/tennis/{tour}/scoreboard"

# Sem User-Agent custom: a fonte aceita o padrao do urllib e recusa os que
# fingem ser navegador. Mesma regra do build_feed.py.


def agora():
    return datetime.now(timezone.utc)


def carimbo():
    return agora().strftime("%Y-%m-%dT%H:%M:%SZ")


def coletar():
    """Foto do que a fonte mostra agora: {id_do_jogo: estado}.

    So o que o contrato publica -- status e games por set. Se mudar aqui,
    muda no arquivo publicado; se nao mudar aqui, o deploy e dispensado.
    """
    foto = {}
    for tour in TOURS:
        with urllib.request.urlopen(URL.format(tour=tour), timeout=30) as resposta:
            dados = json.load(resposta)
        for evento in dados.get("events", []):
            for grupo in evento.get("groupings", []):
                slug = (grupo.get("grouping") or {}).get("slug") or ""
                if "singles" not in slug:
                    continue
                for comp in grupo.get("competitions", []):
                    tipo = comp.get("status", {}).get("type", {})
                    if tipo.get("description") == "Retired":
                        status = "retired"
                    elif tipo.get("description") == "Walkover":
                        status = "walkover"
                    elif tipo.get("name") == "STATUS_SCHEDULED":
                        status = "scheduled"
                    elif tipo.get("state") == "in":
                        status = "inProgress"
                    elif tipo.get("completed"):
                        status = "finished"
                    else:
                        status = "scheduled"
                    placar = []
                    for lado in comp.get("competitors", []):
                        placar.append(tuple(
                            int(linha.get("value") or 0)
                            for linha in (lado.get("linescores") or [])
                        ))
                    nomes = " x ".join(
                        ((lado.get("athlete") or {}).get("shortName")
                         or (lado.get("athlete") or {}).get("displayName") or "?")
                        for lado in comp.get("competitors", [])
                    )
                    foto[str(comp.get("id"))] = {
                        "status": status,
                        "placar": placar,
                        "nomes": nomes,
                        "torneio": evento.get("name"),
                    }
    return foto


def main():
    corta = argparse.ArgumentParser()
    corta.add_argument("--intervalo", type=int, default=60, help="segundos entre coletas")
    corta.add_argument("--saida", default="espn_latencia.jsonl")
    opcoes = corta.parse_args()

    saida = open(opcoes.saida, "a", encoding="utf-8", buffering=1)
    anterior = {}
    ultima_mudanca_placar = {}   # id -> instante da ultima alteracao de games
    latencias = []               # minutos entre ultimo ponto e virada de status
    instantes_de_mudanca = []    # quando o payload mudou (qualquer coisa)
    inicio = agora()
    coletas = 0

    def registrar(tipo, **campos):
        linha = {"t": carimbo(), "tipo": tipo, **campos}
        saida.write(json.dumps(linha, ensure_ascii=False) + "\n")
        return linha

    def resumo(*_):
        duracao = (agora() - inicio).total_seconds() / 60
        print(f"\n\n=== resumo de {duracao:.0f} min, {coletas} coletas ===")
        if latencias:
            print(f"\nlatencia da ESPN (ultimo ponto -> status encerrado), {len(latencias)} jogos:")
            print(f"  min={min(latencias):.1f}min  mediana={statistics.median(latencias):.1f}min  max={max(latencias):.1f}min")
            print("  ATENCAO: e piso, nao valor exato. A resolucao e o proprio")
            print(f"  intervalo de coleta ({opcoes.intervalo}s), e o ultimo ponto so e")
            print("  visivel se a fonte atualiza games durante o set.")
        else:
            print("\nnenhum jogo encerrou durante a coleta -- rode mais tempo,")
            print("ou num horario com jogos terminando.")
        if len(instantes_de_mudanca) > 1:
            gaps = [(b - a).total_seconds() / 60 for a, b in
                    zip(instantes_de_mudanca, instantes_de_mudanca[1:])]
            print(f"\ngranularidade de mudanca do payload, {len(gaps)} intervalos:")
            print(f"  mediana={statistics.median(gaps):.1f}min  max={max(gaps):.1f}min")
            print(f"  => com agendador de 15 min, {sum(1 for g in gaps if g > 15)}/{len(gaps)}"
                  " intervalos passariam de uma janela inteira sem novidade")
            print("  (e nesses o deploy e dispensado e o teste de CDN nao vale)")
        else:
            print("\no payload nao mudou durante a coleta.")
        print(f"\nbruto em {opcoes.saida}")
        saida.close()
        sys.exit(0)

    signal.signal(signal.SIGINT, resumo)
    signal.signal(signal.SIGTERM, resumo)

    print(f"coletando a cada {opcoes.intervalo}s. ctrl-c para o resumo.\n")
    while True:
        try:
            atual = coletar()
        except Exception as erro:  # noqa: BLE001 - coleta longa nao morre por falha de rede
            print(f"{carimbo()} falha na coleta: {erro}")
            registrar("falha", erro=str(erro))
            _sleep(opcoes.intervalo)
            continue

        coletas += 1
        houve_mudanca = False
        for jogo_id, estado in atual.items():
            antes = anterior.get(jogo_id)
            if antes is None:
                continue
            if estado["placar"] != antes["placar"]:
                houve_mudanca = True
                ultima_mudanca_placar[jogo_id] = agora()
                registrar("placar", id=jogo_id, jogo=estado["nomes"],
                          de=str(antes["placar"]), para=str(estado["placar"]))
            if estado["status"] != antes["status"]:
                houve_mudanca = True
                linha = registrar("status", id=jogo_id, jogo=estado["nomes"],
                                  de=antes["status"], para=estado["status"])
                print(f"{linha['t']} {estado['nomes']}: {antes['status']} -> {estado['status']}")
                if estado["status"] in ("finished", "retired", "walkover"):
                    marco = ultima_mudanca_placar.get(jogo_id)
                    if marco:
                        atraso = (agora() - marco).total_seconds() / 60
                        latencias.append(atraso)
                        registrar("latencia", id=jogo_id, jogo=estado["nomes"],
                                  minutos=round(atraso, 1))
                        print(f"    -> {atraso:.1f} min entre o ultimo placar novo e o encerramento")
        if houve_mudanca:
            instantes_de_mudanca.append(agora())
        anterior = atual
        _sleep(opcoes.intervalo)


def _sleep(segundos):
    import time
    time.sleep(segundos)


if __name__ == "__main__":
    main()
