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
VALID_ROUNDS = {"Q", "R1", "R2", "R3", "R4", "R128", "R64", "R32", "R16", "QF", "SF", "F", "RR"}


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


NUMBERED_ROUND = re.compile(r"(?:Round (\d+)|(\d+)(?:st|nd|rd|th) Round)$")


def round_code(comp):
    """Nome da rodada -> chave do contrato, sem estimar nada.

    Rodada numerada ("Round 3", "3rd Round") vira rotulo literal R3 (exibido
    "3R", como ATP e midia fazem): verdade em qualquer torneio, com ou sem
    byes, do primeiro ao ultimo dia. "Round of N" e explicito e mapeia
    direto. Nome desconhecido devolve None e o jogo e descartado com aviso:
    rotulo errado na tela e pior que jogo ausente.
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
    if name in ("Round Robin", "Group Stage"):
        return "RR"
    of_match = re.match(r"Round of (\d+)$", name)
    if of_match:
        return {128: "R128", 64: "R64", 32: "R32", 16: "R16"}.get(int(of_match.group(1)))
    numbered = NUMBERED_ROUND.match(name)
    if numbered:
        number = int(numbered.group(1) or numbered.group(2))
        if 1 <= number <= 4:
            return f"R{number}"
    return None


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
        sets_.append(int(line.get("value") or 0))
        tiebreak = line.get("tiebreak")
        tiebreaks.append(int(tiebreak) if tiebreak is not None else None)
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
    tournaments, seen, dropped = {}, set(), []
    for tour in TOURS:
        payload = fetch(tour)
        for event in payload.get("events", []):
            event_id = "ev-" + str(event.get("id"))
            for grouping in event.get("groupings", []):
                slug = (grouping.get("grouping") or {}).get("slug") or ""
                if "singles" not in slug:
                    continue
                comps = grouping.get("competitions", [])
                gender = "f" if slug.startswith("womens") else "m"
                for comp in comps:
                    comp_id = str(comp.get("id"))
                    if comp_id in seen:
                        continue
                    start = normalize_date(comp.get("date"))
                    if start is None:
                        continue  # jogo sem data nao pertence a dia nenhum na aba
                    code = round_code(comp)
                    if code is None:
                        dropped.append((comp.get("round") or {}).get("displayName"))
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
                        "round": code,
                        "gender": gender,
                        "status": status_of(comp),
                        "startUTC": start,
                        "players": players,
                    })
    total = sum(len(t["matches"]) for t in tournaments.values())
    if dropped:
        print(f"aviso: {len(dropped)} jogos descartados por rodada desconhecida: "
              f"{sorted(set(str(d) for d in dropped))}", file=sys.stderr)
    if total and len(dropped) > total * 0.10:
        raise AssertionError("mais de 10% dos jogos com rodada desconhecida; contrato precisa evoluir")
    return {
        "schemaVersion": 1,
        "enabled": True,
        "generatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tournaments": sorted(tournaments.values(), key=lambda t: t["name"] or ""),
    }


def validate(feed):
    """Valida o mesmo que o Codable do app exige, campo a campo."""
    matches = [m for t in feed["tournaments"] for m in t["matches"]]
    assert feed["schemaVersion"] == 1
    for tournament in feed["tournaments"]:
        assert tournament["name"] and isinstance(tournament["name"], str), tournament["id"]
    for match in matches:
        assert match["status"] in VALID_STATUS, match
        assert match["round"] in VALID_ROUNDS, match
        assert len(match["players"]) == 2, match
        datetime.strptime(match["startUTC"], "%Y-%m-%dT%H:%M:%SZ")
        for player in match["players"]:
            assert player["name"] and isinstance(player["name"], str), match
            assert all(isinstance(s, int) for s in player["sets"]), match
            assert all(t is None or isinstance(t, int) for t in player["tb"]), match
    return len(matches)


def empty_feed_is_suspicious(feed):
    """Zero jogos e entressafra legitima ou solucao da fonte?

    Se o arquivo publicado tem jogos recentes (ultimos 5 dias), um retorno
    subitamente vazio e solucao: melhor falhar e manter o ultimo bom. Se o
    publicado ja esta vazio ou velho, e entressafra: publicar vazio e correto
    e a aba mostra o estado desenhado para isso.
    """
    if any(t["matches"] for t in feed["tournaments"]):
        return False
    try:
        with urllib.request.urlopen(PUBLISHED, timeout=15) as response:
            current = json.load(response)
    except Exception:
        return False
    latest = [m["startUTC"] for t in current.get("tournaments", []) for m in t.get("matches", [])]
    if not latest:
        return False
    newest = max(datetime.strptime(d, "%Y-%m-%dT%H:%M:%SZ") for d in latest if d)
    return (datetime.utcnow() - newest).days < 5


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
    import os
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    feed = build()
    total = validate(feed)
    assert not empty_feed_is_suspicious(feed), "fonte devolveu vazio com o publicado ainda fresco"
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
