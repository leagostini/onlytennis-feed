#!/usr/bin/env python3
"""Gatilho pontual do build-feed, no lugar do cron do GitHub.

Por que isto existe: o cron do GitHub Actions entrega uma fracao dos ticks
agendados. Medido em 77 execucoes de `*/15 * * * *` numa janela de 85,8h:
343 disparos esperados, 77 reais (22%), mediana de 62 min entre execucoes e
pior intervalo de 2h38. Os ticks nao chegam atrasados, simplesmente nao
acontecem -- por isso mexer no horario do cron nao resolve.

O robo, o historico em git e o GitHub Pages ficam exatamente onde estao. Este
servico so troca quem aperta o botao: o Cloud Scheduler chama aqui a cada 15
minutos e daqui sai um workflow_dispatch.

Por que um intermediario em vez de o Scheduler chamar o GitHub direto: o
Cloud Scheduler nao le o Secret Manager. Ele so sabe emitir credencial do
proprio Google (OIDC/OAuth), que o GitHub nao aceita. A unica alternativa
seria escrever o PAT no campo `headers` do job, que e texto puro e sai
inteiro num `gcloud scheduler jobs describe`. Este arquivo existe para o
segredo poder morar no Secret Manager.

O PAT chega montado como variavel de ambiente pelo --set-secrets do deploy;
nao ha cliente do Secret Manager aqui, e a funcao so precisa do papel
secretAccessor sobre um unico segredo.
"""
import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone

import functions_framework

API = "https://api.github.com/repos/{repo}/actions/workflows/{workflow}/dispatches"
PUBLISHED = "https://leagostini.github.io/onlytennis-feed/latest.json"

# Acima disto, com jogo acontecendo, o feed esta parado de verdade.
LIMITE_MIN = int(os.environ.get("LIMITE_ATRASO_MIN", "30"))
# Folga para um jogo que comecou atrasado antes de contar como "devia estar
# se movendo". Era 30 min, e 30 min e pouco para tenis: sessao que atrasa uma
# hora e rotina (chuva, jogo anterior em tres sets, "not before"), e o feed
# publicado agora traz nove jogos de qualifying marcados para o mesmo horario.
# Com 30, uma tarde chuvosa normal acendia o alarme com o sistema inteiro sao,
# e alarme que mente uma vez deixa de ser lido.
FOLGA_INICIO_MIN = 90


def disparar_workflow():
    """Pede ao GitHub para rodar o build-feed agora. 204 = aceito."""
    repo = os.environ["GITHUB_REPO"]
    workflow = os.environ.get("GITHUB_WORKFLOW", "feed.yml")
    ref = os.environ.get("GITHUB_REF", "master")
    requisicao = urllib.request.Request(
        API.format(repo=repo, workflow=workflow),
        data=json.dumps({"ref": ref}).encode(),
        method="POST",
        headers={
            "Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": "onlytennis-feed-dispatcher",
        },
    )
    with urllib.request.urlopen(requisicao, timeout=20) as resposta:
        return resposta.status


def deveria_estar_se_movendo(feed, agora):
    """Existe jogo que obriga o feed a mudar?

    O portao de "sem novidade, sem deploy" faz o generatedAt envelhecer de
    forma legitima na madrugada e na entressafra. Alertar por idade pura
    tocaria o alarme toda noite. So conta como obrigacao de movimento:

      - jogo em andamento; ou
      - jogo marcado cujo horario ja passou (e com horario confiavel, porque
        timeTBD e placeholder de sessao, nao programacao real).

    Jogo encerrado nao entra: depois do ultimo jogo do dia o feed para de
    mudar, e isso e o comportamento correto, nao uma falha.
    """
    for torneio in feed.get("tournaments", []):
        for jogo in torneio.get("matches", []):
            # Jogo suspenso (chuva, escuridao) nao pode mudar por definicao, e
            # a suspensao noturna de um Slam dura uma noite inteira. Sem esta
            # linha o alarme tocaria a noite toda sobre um sistema correto.
            if jogo.get("suspended"):
                continue
            if jogo.get("status") == "inProgress":
                return True
            if jogo.get("status") == "scheduled" and not jogo.get("timeTBD"):
                try:
                    inicio = datetime.strptime(
                        jogo["startUTC"], "%Y-%m-%dT%H:%M:%SZ"
                    ).replace(tzinfo=timezone.utc)
                except (KeyError, TypeError, ValueError):
                    # TypeError cobre `startUTC: null`, que sem isto escapava
                    # para o except de cima e desligava o diagnostico inteiro
                    # em vez de pular um jogo.
                    continue
                if (agora - inicio).total_seconds() / 60 > FOLGA_INICIO_MIN:
                    return True
    return False


def checar_frescor():
    """Diagnostico do que esta publicado, para o alerta do Cloud Logging.

    Roda depois do disparo e nunca derruba a requisicao: se o Pages estiver
    fora do ar ou o JSON vier quebrado, o disparo ja aconteceu e e isso que
    importa. Devolve dicionario que vira log estruturado.
    """
    try:
        with urllib.request.urlopen(PUBLISHED, timeout=15) as resposta:
            feed = json.load(resposta)
        agora = datetime.now(timezone.utc)
        gerado = datetime.strptime(
            feed["generatedAt"], "%Y-%m-%dT%H:%M:%SZ"
        ).replace(tzinfo=timezone.utc)
        idade = round((agora - gerado).total_seconds() / 60, 1)
        com_jogo = deveria_estar_se_movendo(feed, agora)
        return {
            "idadeMin": idade,
            "jogoAtivo": com_jogo,
            "feedParado": com_jogo and idade > LIMITE_MIN,
        }
    except Exception as erro:  # noqa: BLE001 - diagnostico nunca quebra o disparo
        return {"erroChecagem": type(erro).__name__}


@functions_framework.http
def disparar(request):
    """Entrada HTTP. O Scheduler chama com OIDC; nao ha rota publica.

    O diagnostico roda SEMPRE, inclusive quando o disparo falha. Antes ele so
    rodava no caminho feliz, e isso deixava o alerta cego justamente na pior
    falha possivel: token revogado ou repositorio renomeado fazem todo tick
    morrer no 401/404, nenhum log carrega `feedParado`, a metrica fica em zero
    e o feed volta calado a depender do cron de 22%. Falha do disparo e
    exatamente quando queremos saber se ha jogo acontecendo.
    """
    falha = None
    status = None
    try:
        status = disparar_workflow()
    except urllib.error.HTTPError as erro:
        # O corpo vem do GitHub, entao pode ir para o log inteiro.
        falha = {"status": erro.code,
                 "corpo": erro.read().decode("utf-8", "replace")[:300]}
    except Exception as erro:  # noqa: BLE001
        # So o TIPO da excecao. `str(erro)` aqui e caminho de vazamento: se o
        # token for gravado com uma quebra de linha no fim, o http.client
        # levanta ValueError com o valor do header dentro da mensagem, e o
        # token inteiro iria parar no Cloud Logging sem ninguem perceber.
        falha = {"erro": type(erro).__name__}

    diagnostico = checar_frescor()
    # feedParado=true e o que a politica de alerta do Cloud Logging procura;
    # disparoFalhou=true e a segunda politica, a que pega o gatilho morto.
    if falha is not None:
        print(json.dumps({
            "severity": "ERROR",
            "message": "disparo falhou",
            "disparoFalhou": True,
            **falha,
            **diagnostico,
        }))
        return ("disparo falhou", 502)

    print(json.dumps({
        "severity": "WARNING" if diagnostico.get("feedParado")
                    or diagnostico.get("erroChecagem") else "INFO",
        "message": "workflow disparado",
        "disparoFalhou": False,
        "statusGitHub": status,
        **diagnostico,
    }))
    return ("ok", 200)
