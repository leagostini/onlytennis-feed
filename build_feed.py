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
OUT_RANKINGS = "public/rankings.json"
ARCHIVE = "archive/2026.json"
PUBLISHED = "https://leagostini.github.io/onlytennis-feed/latest.json"
PUBLISHED_RANKINGS = "https://leagostini.github.io/onlytennis-feed/rankings.json"
RANKINGS_URL = "https://site.api.espn.com/apis/site/v2/sports/tennis/{tour}/rankings"

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

# Codigos de 3 letras (tenis usa o padrao olimpico; a uniao com ISO3 cobre os dois)
COUNTRY3 = {
    "ARG": "AR", "ARM": "AM", "AUS": "AU", "AUT": "AT", "AZE": "AZ",
    "BAR": "BB", "BEL": "BE", "BIH": "BA", "BLR": "BY", "BOL": "BO",
    "BRA": "BR", "BUL": "BG", "BGR": "BG", "CAN": "CA", "CHI": "CL",
    "CHL": "CL", "CHN": "CN", "COL": "CO", "CRC": "CR", "CRO": "HR",
    "HRV": "HR", "CYP": "CY", "CZE": "CZ", "DEN": "DK", "DNK": "DK",
    "DOM": "DO", "ECU": "EC", "EGY": "EG", "ESA": "SV", "ESP": "ES",
    "EST": "EE", "FIN": "FI", "FRA": "FR", "GBR": "GB", "GEO": "GE",
    "GER": "DE", "DEU": "DE", "GRE": "GR", "GRC": "GR", "HKG": "HK",
    "HUN": "HU", "INA": "ID", "IDN": "ID", "IND": "IN", "IRL": "IE",
    "ISR": "IL", "ITA": "IT", "JAM": "JM", "JOR": "JO", "JPN": "JP",
    "KAZ": "KZ", "KOR": "KR", "LAT": "LV", "LVA": "LV", "LIB": "LB",
    "LBN": "LB", "LTU": "LT", "LUX": "LU", "MAR": "MA", "MAS": "MY",
    "MYS": "MY", "MDA": "MD", "MEX": "MX", "MON": "MC", "MCO": "MC",
    "NED": "NL", "NLD": "NL", "NOR": "NO", "NZL": "NZ", "PAR": "PY",
    "PRY": "PY", "PER": "PE", "PHI": "PH", "PHL": "PH", "POL": "PL",
    "POR": "PT", "PRT": "PT", "ROU": "RO", "RSA": "ZA", "ZAF": "ZA",
    "RUS": "RU", "SER": "RS", "SRB": "RS", "SVK": "SK", "SLO": "SI",
    "SVN": "SI", "ROM": "RO", "AND": "AD", "MNE": "ME", "MKD": "MK",
    "SUI": "CH", "CHE": "CH", "SWE": "SE", "THA": "TH", "TPE": "TW",
    "TWN": "TW", "TUN": "TN", "TUR": "TR", "UKR": "UA", "URU": "UY",
    "URY": "UY", "USA": "US", "UZB": "UZ", "VEN": "VE", "VIE": "VN",
    "VNM": "VN",
}

VALID_STATUS = {"scheduled", "inProgress", "finished", "retired", "walkover"}
VALID_ROUNDS = {"Q", "R1", "R2", "R3", "R4", "R128", "R64", "R32", "R16", "QF", "SF", "F", "RR"}


def fetch(tour):
    # Sem User-Agent custom: a fonte aceita o padrao do urllib e recusa
    # os que fingem ser navegador.
    with urllib.request.urlopen(URL.format(tour=tour), timeout=30) as response:
        return json.load(response)


def fetch_url(url):
    with urllib.request.urlopen(url, timeout=30) as response:
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


def pid_of(athlete):
    """Identificador estavel do jogador, extraido do link do perfil."""
    for link in athlete.get("links") or []:
        found = re.search(r"/id/(\d+)(?:/|$)", link.get("href") or "")
        if found:
            return found.group(1)
    raw = athlete.get("id")
    return str(raw) if raw else None


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
        "pid": pid_of(athlete),
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
                        "major": bool(event.get("major")),
                        "matches": [],
                    })
                    match_entry = {
                        "id": comp_id,
                        "round": code,
                        "gender": gender,
                        "status": status_of(comp),
                        "startUTC": start,
                        "players": players,
                    }
                    court = (comp.get("venue") or {}).get("court")
                    if court:
                        match_entry["court"] = court
                    if comp.get("wasSuspended"):
                        match_entry["suspended"] = True
                    entry["matches"].append(match_entry)
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
    now = datetime.now(timezone.utc).replace(tzinfo=None)  # naive UTC, como o strptime
    return (now - newest).days < 5


def unchanged_pair(feed, rankings):
    return unchanged_from_published(feed) and unchanged_rankings(rankings)


def unchanged_rankings(rankings):
    try:
        with urllib.request.urlopen(PUBLISHED_RANKINGS, timeout=15) as response:
            current = json.load(response)
    except Exception:
        return False
    strip = lambda d: {k: v for k, v in d.items() if k != "generatedAt"}
    return strip(rankings) == strip(current)


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


def build_rankings():
    """Top 150 de cada circuito, com posicao anterior para a setinha."""
    out = {}
    for tour in TOURS:
        payload = fetch_url(RANKINGS_URL.format(tour=tour))
        ranks = (payload.get("rankings") or [{}])[0].get("ranks") or []
        entries = []
        for item in ranks:
            athlete = item.get("athlete") or {}
            name = athlete.get("displayName")
            rank = item.get("current")
            if not name or not isinstance(rank, int):
                continue
            country3 = (athlete.get("citizenshipCountry") or "").upper()
            points = item.get("points")
            entries.append({
                "rank": rank,
                "prev": item.get("previous") if isinstance(item.get("previous"), int) else None,
                "points": int(points) if isinstance(points, (int, float)) else None,
                "pid": pid_of(athlete),
                "name": name,
                "country": COUNTRY3.get(country3),
                "age": athlete.get("age") if isinstance(athlete.get("age"), int) else None,
            })
        out[tour] = sorted(entries, key=lambda e: e["rank"])
    return {
        "schemaVersion": 1,
        "generatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "atp": out.get("atp", []),
        "wta": out.get("wta", []),
    }


def validate_rankings(rankings):
    for tour in ("atp", "wta"):
        entries = rankings[tour]
        assert entries, f"ranking {tour} veio vazio"
        for entry in entries:
            assert entry["name"] and isinstance(entry["rank"], int), entry
    return len(rankings["atp"]) + len(rankings["wta"])


def rankings_or_published():
    """Ranking novo; se a fonte falhar, o publicado e reaproveitado.

    O deploy do Pages substitui o site INTEIRO: publicar sem rankings.json
    faria a tela de ranking do app dar 404. Sem novo e sem publicado, a run
    falha e o site anterior continua no ar.
    """
    try:
        fresh = build_rankings()
        validate_rankings(fresh)
        return fresh
    except Exception as error:
        print(f"aviso: ranking novo falhou ({error}); reaproveitando o publicado",
              file=sys.stderr)
        current = fetch_url(PUBLISHED_RANKINGS)
        validate_rankings(current)
        return current


def merge_archive(feed):
    """Acumula resultados encerrados em archive/2026.json (commitado pelo
    workflow). E a memoria que a fonte nao da: base futura de confronto
    direto e forma recente. Devolve quantos jogos novos entraram."""
    try:
        with open(ARCHIVE, encoding="utf-8") as handle:
            archive = json.load(handle)
    except FileNotFoundError:
        archive = {"schemaVersion": 1, "matches": {}}
    known = archive["matches"]
    added = 0
    for tournament in feed["tournaments"]:
        for match in tournament["matches"]:
            if match["status"] not in ("finished", "retired", "walkover"):
                continue
            if match["id"] in known:
                continue
            known[match["id"]] = {
                "t": tournament["name"], "tour": tournament["tour"],
                "gender": match["gender"], "round": match["round"],
                "d": match["startUTC"], "status": match["status"],
                "players": match["players"],
            }
            added += 1
    if added:
        import os
        os.makedirs(os.path.dirname(ARCHIVE), exist_ok=True)
        with open(ARCHIVE, "w", encoding="utf-8") as handle:
            json.dump(archive, handle, separators=(",", ":"), ensure_ascii=False)
    return added


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
    rankings = rankings_or_published()
    archived = merge_archive(feed)
    changed = not unchanged_pair(feed, rankings)
    with open(OUT, "w", encoding="utf-8") as handle:
        json.dump(feed, handle, separators=(",", ":"), ensure_ascii=False)
    with open(OUT_RANKINGS, "w", encoding="utf-8") as handle:
        json.dump(rankings, handle, separators=(",", ":"), ensure_ascii=False)
    emit_output(changed)
    label = "novidade" if changed else "sem mudanca, deploy dispensado"
    print(f"ok: {len(feed['tournaments'])} torneios, {total} jogos, "
          f"ranking {len(rankings['atp'])}+{len(rankings['wta'])}, "
          f"{archived} arquivados ({label})")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:  # noqa: BLE001 - qualquer falha segura o deploy
        print(f"falha, nada publicado: {error}", file=sys.stderr)
        sys.exit(1)
