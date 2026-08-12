# onlytennis-feed

Robô do feed da aba Circuito do app Only Tennis: calendário e resultados de
simples de ATP e WTA, sem placar ao vivo.

`build_feed.py` busca a fonte, valida e publica `public/latest.json` no
GitHub Pages. Falha de fonte ou de validação nunca publica nada (o último
arquivo bom continua no ar), e dado sem novidade dispensa o deploy.

Quem dispara é o Cloud Scheduler, a cada 15 minutos, via `workflow_dispatch`.
O `schedule` do workflow continua ligado como contingência, mas não dá para
contar com ele: medido em 77 execuções, entrega 22% dos ticks, com mediana de
62 minutos entre execuções. Os detalhes, o runbook e os scripts de medição
estão em [`ops/`](ops/README.md).

O contrato do arquivo (schemaVersion 1) está descrito no plano do app,
`PLANO_CIRCUITO_PRO.md`. O campo `enabled: false` é o interruptor remoto que
liga o estado de manutenção da aba.
