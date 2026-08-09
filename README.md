# onlytennis-feed

Robô do feed da aba Circuito do app Only Tennis: calendário e resultados de
simples de ATP e WTA, sem placar ao vivo.

A cada 15 minutos o GitHub Actions roda `build_feed.py`, que busca a fonte,
valida e publica `public/latest.json` no GitHub Pages. Falha de fonte ou de
validação nunca publica nada (o último arquivo bom continua no ar), e dado
sem novidade dispensa o deploy.

O contrato do arquivo (schemaVersion 1) está descrito no plano do app,
`PLANO_CIRCUITO_PRO.md`. O campo `enabled: false` é o interruptor remoto que
liga o estado de manutenção da aba.
