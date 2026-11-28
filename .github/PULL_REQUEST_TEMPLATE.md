## Descrição

<!-- O que esse PR faz? Por quê? -->

## Issue/Tarefa relacionada

<!-- Link do Jira / número da issue -->
Jira:

## Requisito relacionado

<!-- Preenchido automaticamente pela Action a partir dos labels RF/RNF/D do card no Jira. Se vier vazio, o card ainda não tem label — edite aqui manualmente com o código da Matriz de Rastreabilidade. -->
Requisito: _(preenchido automaticamente)_

## Tipo de mudança

- [ ] feat (nova funcionalidade)
- [ ] fix (correção de bug)
- [ ] docs (documentação)
- [ ] refactor (refatoração sem mudança de comportamento)
- [ ] test (testes)
- [ ] chore (config, build, dependências)

## Como testar

<!-- Passo a passo para quem for revisar conseguir validar -->
1.
2.

## Plano de testes

<!-- Escreva antes de começar a codar e complete antes de pedir revisão. Uma linha por critério de aceite do card.
Unidade no back = função Python com regra de negócio. Regras completas: https://github.com/DenariusData/API-6SEM#tests
PR sem mudança de comportamento (docs, ci, chore): escreva "Não se aplica" e o motivo. -->

| Critério de aceite | Unidade (arquivo → função) | Teste (arquivo → nome) | Tipo |
|---|---|---|---|
| | | | Unitário / Integração |

## Checklist de testes

- [ ] Todo critério de aceite do card está no plano com pelo menos um teste
- [ ] Todo caminho (`if`/`else`) e erro tratado das unidades alteradas tem teste
- [ ] Os testes unitários não usam banco, rede nem relógio real (o que é externo está mockado)
- [ ] Cada teste confere um comportamento, segue Arrange/Act/Assert e o nome diz o resultado esperado
- [ ] Vi cada teste novo falhar uma vez (quebrando o valor esperado ou o código)
- [ ] `pytest tests/unit` passa localmente
- [ ] Correção de bug: incluí um teste que falhava antes da correção

## Checklist

- [ ] Testei localmente antes de abrir o PR
- [ ] Título do PR segue o padrão (`feat: ...`, `fix: ...`, em inglês)
- [ ] Não deixei `console.log`/`print` de debug esquecidos
- [ ] Atualizei o `.env.example` se adicionei variável de ambiente nova
- [ ] Não há conflitos com a branch de destino
