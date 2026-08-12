# Gatilho e medições

Tudo aqui roda **na sua máquina**, não no CI. O repositório e o robô não mudam.

## O problema, medido

O cron do GitHub Actions entrega uma fração dos ticks. Em 77 execuções de
`*/15 * * * *` numa janela de 85,8h:

| | |
|---|---|
| disparos esperados / reais | 343 / 77 (**22%**) |
| mediana entre execuções | **62 min** (configurado: 15) |
| pior intervalo | **2h38** |
| falhas de execução | **0 de 77** |
| entrega por hora do dia | uniforme (2–5/h em todas) |

Os intervalos são múltiplos quase exatos de 15 min, então os ticks que
disparam estão na grade certa — os outros não acontecem. Não é atraso, é
descarte, e é constante ao longo do dia. **Mexer no horário do cron não
resolve.** O robô em si nunca falhou, e o pipeline inteiro (buscar, validar,
publicar no Pages) leva **27 segundos**.

A correção é trocar só o gatilho: Cloud Scheduler → Cloud Function → Secret
Manager → `workflow_dispatch`. O cron atual fica como contingência.

## Ordem

As medições vêm primeiro porque a 3 pode encolher o trabalho no app. Mas
**a etapa 1 não depende de nenhuma delas** — se quiser destravar a latência
hoje, faça 1 e meça em paralelo.

| | etapa | depende de |
|---|---|---|
| 1 | subir o gatilho | nada |
| 2 | medir a ESPN | dia de torneio |
| 3 | medir o CDN | uma publicação real (etapa 1 ajuda) |
| 4 | alerta | etapa 1 |
| 5 | app | etapa 3 |

---

## 1. Subir o gatilho

**Já está no ar** no projeto `only-tennis`, região `us-central1`: segredo,
as duas contas de serviço, a função e o job. O que falta é o token, e ele é
o único passo que não dá para automatizar — o GitHub não emite PAT por API.

| | |
|---|---|
| projeto / região | `only-tennis` / `us-central1` |
| conta usada | `leandro.contact@gmail.com` (owner) |
| função | `feed-dispatcher`, sem acesso público |
| job | `feed-15min`, **pausado** até o PAT chegar |
| segredo | `onlytennis-feed-pat`, versão 1 = placeholder |

Crie o **PAT fine-grained** em github.com/settings/personal-access-tokens:

- Repository access: **somente** `leagostini/onlytennis-feed`
- Permissions → **Actions: Read and write**. Só isso. Nada de `contents`.
- Expiração: 90 dias (anote para renovar — veja "Rotação" no fim)

Grave o token (a digitação fica oculta e nada vai parar no histórico do
shell):

```bash
printf 'Cole o PAT: '; read -rs PAT; echo; printf '%s' "$PAT" | CLOUDSDK_BILLING_QUOTA_PROJECT=only-tennis gcloud secrets versions add onlytennis-feed-pat --data-file=- --project=only-tennis --account=leandro.contact@gmail.com; unset PAT
```

O segredo entra na função como variável de ambiente, resolvida quando a
instância sobe — então depois de gravar é preciso uma revisão nova e soltar
o job:

```bash
CONTA=leandro.contact@gmail.com PROJECT=only-tennis GRAVAR_PAT=0 ./ops/deploy.sh
gcloud scheduler jobs resume feed-15min --location=us-central1 --project=only-tennis
gcloud scheduler jobs run feed-15min --location=us-central1 --project=only-tennis
```

Para montar tudo do zero em outro projeto, o `deploy.sh` faz o caminho
inteiro sozinho (`GRAVAR_PAT=1` pede o token com digitação oculta): cria o
segredo, **duas** contas de serviço separadas (a função lê o segredo mas não
se invoca; o scheduler invoca mas nunca vê o segredo), a função sem acesso
público e o job de 15 minutos.

Teste:

```bash
gcloud functions logs read feed-dispatcher --region=us-central1 --project=only-tennis --limit=5
```

Confira que apareceu execução nova em Actions. A partir daí a mediana entre
execuções deve cair de 62 para ~15 minutos.

## 2. Medir a ESPN

Num dia de torneio, algumas horas:

```bash
python3 ops/medir_espn.py --intervalo 60
# ctrl-c para o resumo
```

Devolve duas coisas: quanto a fonte demora entre o último ponto e marcar
encerrado (o que sobra do orçamento de 30 min), e com que frequência o
payload muda de verdade — que é o que diz se "rodar durante jogo ao vivo"
produz publicação ou não.

## 3. Medir o CDN

```bash
./ops/medir_cdn.sh
```

Ele aquece a borda, espera você disparar, e coleta por 12 minutos comparando
a URL normal com uma URL de parâmetro único que lê a origem.

**Pré-condição:** a execução precisa ter os dois jobs, `build` **e** `deploy`.
Só `build` significa deploy dispensado por falta de novidade, e o teste não
vale. Repita em **pelo menos três publicações** — o Fastly espalha requisições
entre nós e uma coleta só não representa a CDN.

Resultado manda na etapa 5:

- linhas `ANTIGO` por vários minutos → borda não é purgada → app precisa de cache-busting
- tudo `igual` desde o começo → borda é purgada → basta revalidação forçada

## 4. Alerta

A função já emite `feedParado` no log estruturado. Ele só fica `true` quando
há jogo em andamento (ou marcado cujo horário já passou) **e** o publicado
está parado há mais de 30 min. Noite tranquila e entressafra nunca disparam,
que é o erro clássico de alertar por idade pura.

A métrica `feed_parado` **já está criada** no projeto `only-tennis` (foi
assim):

```bash
gcloud logging metrics create feed_parado \
  --description="feed sem atualizar com jogo acontecendo" \
  --log-filter='resource.type="cloud_run_revision"
    resource.labels.service_name="feed-dispatcher"
    jsonPayload.feedParado=true'
```

Falta a política de alerta sobre ela: condição "any time series > 0" numa
janela de 30 min, com um canal de e-mail. O canal precisa ser verificado
pelo dono da caixa (o Google manda um e-mail de confirmação), por isso não
foi criado junto. O limite é ajustável sem redeploy pela variável
`LIMITE_ATRASO_MIN`.

## 5. App

Esse repositório não está nesta sessão, então aqui vai a especificação, não o
código. Dois pontos em `ProTourFeedService.swift` e `ProTourView.swift`:

**Política de cache.** Hoje o `URLSession` padrão obedece ao `max-age=600` do
Pages, então o app pode receber os mesmos bytes por até 10 minutos sem sequer
tocar na rede. Revalidação por ETag sozinha **não** resolve: um cache não
revalida entrada ainda fresca, o condicional só entra em cena depois de ela
vencer. O que resolve é forçar:

```swift
var pedido = URLRequest(url: url)
pedido.cachePolicy = .reloadRevalidatingCacheData
```

Assim toda chamada manda o condicional: 304 barato quando não mudou, 200
fresco quando mudou. Correto independente do resultado da etapa 3.

**Atualização com a aba visível.** Timer de ~2 min iniciado quando a aba
aparece e invalidado quando some, mais uma busca ao voltar do background
(`scenePhase == .active`). Sem timer rodando com a aba fechada.

**Cache-busting — só se a etapa 3 pedir.** Deixe atrás de um booleano; se a
borda for purgada no deploy, ele nunca é ligado. Custa banda: URL nova nunca
dá 304, todo poll vira download completo.

```swift
if precisaFurarCDN {   // ligar apenas se a etapa 3 mostrar borda suja
    url.append(queryItems: [.init(name: "t", value: String(Int(Date().timeIntervalSince1970) / 60))])
}
```

## Meta

**Resultado encerrado aparece no app em até 30 minutos.** Não "o robô roda a
cada 15 minutos" — o número que o usuário sente é o primeiro. Só feche o
compromisso depois da etapa 2: com agendador de 15 min o erro de amostragem
já é 0–15 min, e se a fonte for lenta a folga some.

## Custo

96 invocações/dia (~2.900/mês) de ~200 ms. Dentro da franquia gratuita do
Cloud Run e dos três jobs gratuitos do Scheduler. Repositório público, então
os minutos de Actions seguem gratuitos.

## Rotação do PAT

O `deploy.sh` adiciona **versão nova** ao mesmo segredo, e a função usa
`:latest`. Para trocar o token sem mexer em mais nada:

```bash
CONTA=leandro.contact@gmail.com PROJECT=only-tennis GRAVAR_PAT=1 ./ops/deploy.sh
```

O redeploy junto é de propósito: sem revisão nova, a instância que já está
de pé continua com o token velho até reciclar.

## Voltar atrás

O cron nunca foi removido, então basta desligar o job — o sistema volta ao
comportamento anterior sozinho:

```bash
gcloud scheduler jobs pause feed-15min --location=us-central1 --project=only-tennis
```

Rodar duas vezes ao mesmo tempo é seguro: `merge_archive()` descarta id de
jogo já conhecido e `changed` é comparação de conteúdo, então a segunda
execução não acha novidade, não commita e não faz deploy. Além disso o
`concurrency: group: pages` do workflow serializa as execuções, e por isso o
`git push` do passo de arquivo não tem como dar conflito.
