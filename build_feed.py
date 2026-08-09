#!/usr/bin/env python3
"""Monta o public/latest.json da aba Circuito do Only Tennis.

Contrato v1 (PLANO_CIRCUITO_PRO.md, secao 5 do repo do app): calendario e
resultados de simples de ATP e WTA, sem placar ao vivo. Roda a cada 15 minutos
no GitHub Actions, 24h por dia (o tenis acontece em todos os fusos).

Regra de ouro: em qualquer falha (fonte fora do ar, formato inesperado,
validacao reprovada) o script sai com erro SEM escrever o arquivo. O deploy
nao acontece e o GitHub Pages continua servindo o ultimo arquivo bom.
"""
import json
import re
import sys
import urllib.request
from datetime import datetime, timezone

TOURS = ["atp", "wta"]
URL = "https://site.api.espn.com/apis/site/v2/sports/tennis/{tour}/scoreboard"
OUT = "public/latest.json"
PUBLISHED = "https://leagostini.github.io/onlytennis-feed/latest.json"

# Pais por extenso (como vem da fonte) -> ISO-3166 alpha-2 (como o app espera).
# Pais fora da tabela vira null e o app simplesmente nao desenha bandeira.
ISO = {
    "Argentina": "AR", "Armenia": "AM", "Australia": "AU", "Austria": "AT",
    "Azerbaijan": "AZ", "Belarus": "BY", "Belgium": "BE", "Bolivia": "BO",
    "Bosnia and Herzegovina": "BA", "Brazil": "BR", "Bulgaria": "BG",
    "Canada": "CA", "Chile": "CL", "China": "CN", "Chinese Taipei": "TW",
    "Colombia": "CO", "Croatia": "HR", "Cyprus": "CY", "Czech Republic": "CZ",
    "Czechia": "CZ", "Denmark": "DK", "Dominican Republic": "DO",
    "Ecuador": "EC", "Egypt": "EG", "El Salvador": "SV", "Estonia": "EE",
    "Finland": "FI", "France": "FR", "Georgia": "GE", "Germany": "DE",
    "Great Britain": "GB", "Greece": "GR", "Hong Kong": "HK", "Hungary": "HU",
    "India": "IN", "Indonesia": "ID", "Ireland": "IE", "Israel": "IL",
    "Italy": "IT", "Jamaica": "JM", "Japan": "JP", "Jordan": "JO",
    "Kazakhstan": "KZ", "Korea": "KR", "Latvia": "LV", "Lebanon": "LB",
    "Lithuania": "LT", "Luxembourg": "LU", "Malaysia": "MY", "Mexico": "MX",
    "Moldova": "MD", "Monaco": "MC", "Morocco": "MA", "Netherlands": "NL",
    "New Zealand": "NZ", "Norway": "NO", "Paraguay": "PY", "Peru": "PE",
    "Philippines": "PH", "Poland": "PL", "Portugal": "PT", "Romania": "RO",
    "Russia": "RU", "Serbia": "RS", "Slovakia": "SK", "Slovenia": "SI",
    "South Africa": "ZA", "South Korea": "KR", "Spain": "ES", "Sweden": "SE",
    "Switzerland": "CH", "Taiwan": "TW", "Thailand": "TH", "Tunisia": "TN",
    "Turkey": "TR", "Turkiye": "TR", "Ukraine": "UA", "United Kingdom": "GB",
    "United States": "US", "Uruguay": "UY", "USA": "US", "Uzbekistan": "UZ",
    "Venezuela": "VE", "Vietnam": "VN",
}

VALID_STATUS = {"scheduled", "inProgress", "finished", "retired", "walkover"}
VALID_ROUNDS = {"Q", "R128", "R64", "R32", "R16", "QF", "SF", "F"}


def fetch(tour):
    # Sem User-Agent custom: a fonte aceita o padrao do urllib e recusa
    # os que fingem ser navegador.
    with urllib.request.urlopen(URL.format(tour=tour), timeout=30) as response:
        return json.load(response)


def status_of(comp):
    kind = comp.get("status", {}).get("type", {})
    description = kind.get("description", "")
    if description == "Retired":
        return "retired"
    if description == "Walkover":
        return "walkover"
    if kind.get("name") == "STATUS_SCHEDULED":
        return "scheduled"
    if kind.get("state") == "in":
        return "inProgress"
    if kind.get("completed"):
        return "finished"
    return "scheduled"


def round_code(comp, max_numbered_round):
    """Nome da rodada -> chave do contrato.

    "Round N" e relativo ao tamanho da chave, entao conta de tras para
    frente: o maior N do torneio encosta nas quartas (R16), o anterior e
    R32, e assim por diante.
    """
    name = (comp.get("round") or {}).get("displayName") or ""
    if name in ("Quarterfinal", "Quarterfinals"):
        return "QF"
    if name in ("Semifinal", "Semifinals"):
        return "SF"
    if name == "Final":
        return "F"
    if name.startswith("Qualifying"):
        return "Q"
    match = re.match(r"Round (?:of )?(\d+)$", name)
    if not match:
        return "R32"
    number = int(match.group(1))
    if name.startswith("Round of"):
        return f"R{number}" if f"R{number}" in VALID_ROUNDS else "R32"
    distance = (max_numbered_round or number) - number
    return {0: "R16", 1: "R32", 2: "R64", 3: "R128"}.get(distance, "R128")


def normalize_date(timestamp):
    if not timestamp:
        return None
    return re.sub(r"T(\d\d):(\d\d)Z$", r"T\1:\2:00Z", timestamp)


def player_of(competitor):
    athlete = competitor.get("athlete") or {}
    name = athlete.get("displayName")
    if not name:
        return None
    sets_, tiebreaks = [], []
    for line in competitor.get("linescores") or []:
        sets_.append(int(line.get("value", 0)))
        tiebreaks.append(line.get("tiebreak"))
    seed = (competitor.get("curatedRank") or {}).get("current")
    if seed is not None and not 0 < seed <= 40:
        seed = None
    return {
        "name": name,
        "country": ISO.get((athlete.get("flag") or {}).get("alt") or ""),
        "seed": seed,
        "sets": sets_,
        "tb": tiebreaks,
        "winner": bool(competitor.get("winner")),
    }


def build():
    tournaments, seen = {}, set()
    for tour in TOURS:
        payload = fetch(tour)
        for event in payload.get("events", []):
            event_id = "ev-" + str(event.get("id"))
            for grouping in event.get("groupings", []):
                slug = (grouping.get("grouping") or {}).get("slug") or ""
                if "singles" not in slug:
                    continue
                comps = grouping.get("competitions", [])
                numbered = [
                    int(m.group(1))
                    for comp in comps
                    if (m := re.match(r"Round (\d+)$",
                                      (comp.get("round") or {}).get("displayName") or ""))
                ]
                max_round = max(numbered) if numbered else None
                gender = "f" if slug.startswith("womens") else "m"
                for comp in comps:
                    comp_id = str(comp.get("id"))
                    if comp_id in seen:
                        continue
                    players = [player_of(c) for c in comp.get("competitors", [])]
                    if len(players) != 2 or None in players:
                        continue
                    seen.add(comp_id)
                    entry = tournaments.setdefault(event_id, {
                        "id": event_id,
                        "name": event.get("name"),
                        "tour": tour,
                        "matches": [],
                    })
                    entry["matches"].append({
                        "id": comp_id,
                        "round": round_code(comp, max_round),
                        "gender": gender,
                        "status": status_of(comp),
                        "startUTC": normalize_date(comp.get("date")),
                        "players": players,
                    })
    return {
        "schemaVersion": 1,
        "enabled": True,
        "generatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tournaments": sorted(tournaments.values(), key=lambda t: t["name"] or ""),
    }


def validate(feed):
    matches = [m for t in feed["tournaments"] for m in t["matches"]]
    assert feed["schemaVersion"] == 1
    assert feed["tournaments"], "nenhum torneio veio da fonte"
    assert matches, "nenhum jogo veio da fonte"
    for match in matches:
        assert match["status"] in VALID_STATUS, match
        assert match["round"] in VALID_ROUNDS, match
        assert len(match["players"]) == 2, match
        if match["startUTC"] is not None:
            datetime.strptime(match["startUTC"], "%Y-%m-%dT%H:%M:%SZ")
    return len(matches)


def unchanged_from_published(feed):
    """O que acabou de sair da fonte e identico ao que ja esta no ar?

    Ignora o carimbo generatedAt (que muda sempre). Madrugada sem jogo novo
    nao gera deploy nenhum: menos trabalho para o GitHub Pages e um selo
    "atualizado ha X" que so anda quando ha novidade de verdade.
    """
    try:
        with urllib.request.urlopen(PUBLISHED, timeout=15) as response:
            current = json.load(response)
    except Exception:
        return False
    strip = lambda d: {k: v for k, v in d.items() if k != "generatedAt"}
    return strip(feed) == strip(current)


def emit_output(changed):
    """Conta ao workflow se ha novidade (arquivo GITHUB_OUTPUT do Actions)."""
    import os
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(f"changed={'true' if changed else 'false'}\n")


def main():
    feed = build()
    total = validate(feed)
    changed = not unchanged_from_published(feed)
    with open(OUT, "w", encoding="utf-8") as handle:
        json.dump(feed, handle, separators=(",", ":"), ensure_ascii=False)
    emit_output(changed)
    label = "novidade" if changed else "sem mudanca, deploy dispensado"
    print(f"ok: {len(feed['tournaments'])} torneios, {total} jogos ({label})")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:  # noqa: BLE001 - qualquer falha segura o deploy
        print(f"falha, nada publicado: {error}", file=sys.stderr)
        sys.exit(1)
