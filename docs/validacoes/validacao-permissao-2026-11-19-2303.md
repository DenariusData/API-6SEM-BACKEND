# Validação do filtro de permissão: mesma pergunta, dois perfis

- **Gerado em:** 19/11/2026 23:03 (horário de Brasília)
- **Pergunta:** Qual é o período de validade da certificação concedida por essa especificação?
- **Idioma:** pt-BR
- **Modelos:** embedding `bge-m3`, LLM `llama3.2`
- **Parâmetros:** similaridade mínima 0.5, até 5 trechos por resposta

## Resultado

| # | Critério | Resultado | Detalhe |
| --- | --- | --- | --- |
| 1 | Mesma pergunta feita com os usuários de Engenharia e de Qualidade | PASSOU | “Qual é o período de validade da certificação concedida por essa especificação?” (pt-BR) para Carlos Eduardo e Ana Souza. |
| 2 | Fontes diferentes conforme a permissão de cada um | PASSOU | 0 documento(s) só para Carlos Eduardo, 1 só para Ana Souza, 0 em comum. |
| 3 | Nenhum trecho fora da permissão aparece na resposta | PASSOU | Nenhuma fonte de nenhum dos dois fica fora da regra de permissão (conferida por fora da query) nem coincide com um trecho bloqueado. |

## Permissões dos usuários demo

Regra: usuário + área + nível de sigilo máximo; sem herança por setor nem por papel.

| Usuário | E-mail | Área | Nível máximo |
| --- | --- | --- | --- |
| Carlos Eduardo | engenharia@akaer.com.br | Manutenção | RESTRITO |
| Carlos Eduardo | engenharia@akaer.com.br | Operações | INTERNO |
| Carlos Eduardo | engenharia@akaer.com.br | Segurança | PUBLICO |
| Ana Souza | qualidade@akaer.com.br | Compliance | CONFIDENCIAL |
| Ana Souza | qualidade@akaer.com.br | Manutenção | PUBLICO |
| Ana Souza | qualidade@akaer.com.br | Segurança | INTERNO |

## Acervo disponível e quem enxerga cada documento

| Documento | Título | Áreas | Sigilo | Trechos | Carlos Eduardo | Ana Souza |
| --- | --- | --- | --- | --- | --- | --- |
| CxP 70000 | Constellation Architecture Requirements Document (CARD) | Segurança | INTERNO | 0 | não | sim |
| FAA-C-1217f | Electrical Work, Interior | Operações | INTERNO | 57 | sim | não |
| JAXA-QTS-2030B | Semiconductor Devices, High Reliability, Space Use, General Specification (o “for” que sobra na capa pode cortar) | Compliance | INTERNO | 36 | não | sim |
| TESTE-624093 | Documento de teste 624093 | Manutenção | INTERNO | 0 | sim | não |

## Fontes devolvidas a cada perfil

### Carlos Eduardo (engenharia@akaer.com.br)

Nenhuma fonte (a IA respondeu que não encontrou).

### Ana Souza (qualidade@akaer.com.br)

| # | Origem | Documento | Título | Áreas | Sigilo | Pág. | Similar. | Citada | Dentro da regra |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | Documento do sistema | JAXA-QTS-2030B | Semiconductor Devices, High Reliability, Space Use, General Specification (o “for” que sobra na capa pode cortar) | Compliance | INTERNO | 9 | 0.619 | sim | sim |
| 2 | Documento do sistema | JAXA-QTS-2030B | Semiconductor Devices, High Reliability, Space Use, General Specification (o “for” que sobra na capa pode cortar) | Compliance | INTERNO | 8-9 | 0.587 | não | sim |
| 3 | Documento do sistema | JAXA-QTS-2030B | Semiconductor Devices, High Reliability, Space Use, General Specification (o “for” que sobra na capa pode cortar) | Compliance | INTERNO | 3-5 | 0.58 | não | sim |
| 4 | Base pública (EverySpec) | DOE G 413.3-10 | DOE G 413.3-10, EARNED VALUE MANAGEMENT SYSTEM (EVMS) (06 MAY 2008) | - | sem sigilo | 17-18 | 0.568 | não | n/a (pública) |
| 5 | Base pública (EverySpec) | DOE G 413.3-10 | DOE G 413.3-10, EARNED VALUE MANAGEMENT SYSTEM (EVMS) (06 MAY 2008) | - | sem sigilo | 11-12 | 0.564 | não | n/a (pública) |

## O que o filtro bloqueou

Busca de controle, SEM filtro: trechos de documentos do sistema que passam da similaridade mínima e que o perfil não pode acessar. Eles seriam usados se não houvesse permissão, e não aparecem nas fontes.

### Bloqueados para Carlos Eduardo

| Documento | Título | Áreas | Sigilo | Pág. | Similar. | Motivo |
| --- | --- | --- | --- | --- | --- | --- |
| JAXA-QTS-2030B | Semiconductor Devices, High Reliability, Space Use, General Specification (o “for” que sobra na capa pode cortar) | Compliance | INTERNO | 9 | 0.619 | sem permissão em Compliance |
| JAXA-QTS-2030B | Semiconductor Devices, High Reliability, Space Use, General Specification (o “for” que sobra na capa pode cortar) | Compliance | INTERNO | 8-9 | 0.587 | sem permissão em Compliance |
| JAXA-QTS-2030B | Semiconductor Devices, High Reliability, Space Use, General Specification (o “for” que sobra na capa pode cortar) | Compliance | INTERNO | 3-5 | 0.58 | sem permissão em Compliance |
| JAXA-QTS-2030B | Semiconductor Devices, High Reliability, Space Use, General Specification (o “for” que sobra na capa pode cortar) | Compliance | INTERNO | 9-10 | 0.557 | sem permissão em Compliance |
| JAXA-QTS-2030B | Semiconductor Devices, High Reliability, Space Use, General Specification (o “for” que sobra na capa pode cortar) | Compliance | INTERNO | 21 | 0.545 | sem permissão em Compliance |
| JAXA-QTS-2030B | Semiconductor Devices, High Reliability, Space Use, General Specification (o “for” que sobra na capa pode cortar) | Compliance | INTERNO | 4-6 | 0.542 | sem permissão em Compliance |
| JAXA-QTS-2030B | Semiconductor Devices, High Reliability, Space Use, General Specification (o “for” que sobra na capa pode cortar) | Compliance | INTERNO | 5-6 | 0.54 | sem permissão em Compliance |
| JAXA-QTS-2030B | Semiconductor Devices, High Reliability, Space Use, General Specification (o “for” que sobra na capa pode cortar) | Compliance | INTERNO | 7-8 | 0.532 | sem permissão em Compliance |
| JAXA-QTS-2030B | Semiconductor Devices, High Reliability, Space Use, General Specification (o “for” que sobra na capa pode cortar) | Compliance | INTERNO | 22-23 | 0.523 | sem permissão em Compliance |
| JAXA-QTS-2030B | Semiconductor Devices, High Reliability, Space Use, General Specification (o “for” que sobra na capa pode cortar) | Compliance | INTERNO | 22-23 | 0.52 | sem permissão em Compliance |

### Bloqueados para Ana Souza

| Documento | Título | Áreas | Sigilo | Pág. | Similar. | Motivo |
| --- | --- | --- | --- | --- | --- | --- |
| FAA-C-1217f | Electrical Work, Interior | Operações | INTERNO | 39 | 0.566 | sem permissão em Operações |
| FAA-C-1217f | Electrical Work, Interior | Operações | INTERNO | 10-11 | 0.532 | sem permissão em Operações |
| FAA-C-1217f | Electrical Work, Interior | Operações | INTERNO | 6-8 | 0.518 | sem permissão em Operações |
| FAA-C-1217f | Electrical Work, Interior | Operações | INTERNO | 9-10 | 0.518 | sem permissão em Operações |
| FAA-C-1217f | Electrical Work, Interior | Operações | INTERNO | 39-41 | 0.514 | sem permissão em Operações |
| FAA-C-1217f | Electrical Work, Interior | Operações | INTERNO | 21 | 0.507 | sem permissão em Operações |
| FAA-C-1217f | Electrical Work, Interior | Operações | INTERNO | 38-39 | 0.504 | sem permissão em Operações |
| FAA-C-1217f | Electrical Work, Interior | Operações | INTERNO | 10 | 0.502 | sem permissão em Operações |
| FAA-C-1217f | Electrical Work, Interior | Operações | INTERNO | 1-5 | 0.502 | sem permissão em Operações |

## Respostas da IA

### Carlos Eduardo

> Não encontrei essa informação nos documentos consultados.

### Ana Souza

> De acordo com o documento [1], p. 9, o período de validade da certificação concedida é de três anos.

## Como reproduzir

```
python manage.py seed_taxonomia
python manage.py seed_usuarios_demo
python manage.py validar_permissao_demo --pergunta "Qual é o período de validade da certificação concedida por essa especificação?"
```

Para os prints da Sprint Review: entrar na tela da IA como `engenharia@akaer.com.br` e como `qualidade@akaer.com.br`, fazer a pergunta acima e capturar a resposta com a lista de fontes de cada um.
