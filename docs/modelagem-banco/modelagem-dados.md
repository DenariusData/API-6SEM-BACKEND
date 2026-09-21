# AKA-45 — Modelagem de Dados: Banco de Documentos (AKAER)

> Este documento reflete as respostas da AKAER (Rodadas 1 e 2 — ver `perguntas-respostas-akaer.pdf`). Onde uma decisão do cliente ainda está em aberto, isso é registrado explicitamente na Seção 6, em vez de resolvido por hipótese.

## 1. Modelagem lógica

### 1.1 Cadastro e taxonomia

| Entidade | Descrição | Cardinalidade |
|---|---|---|
| `Usuario` | Cadastro de quem usa o sistema | 1 Usuario → N Documentos (papéis diferentes: upload, responsável, aprovador) |
| `Area` | Nível 1 da hierarquia de classificação | 1 Area → N Categorias |
| `Categoria` | Nível 2, pertence a uma Area | 1 Categoria → N Subcategorias |
| `Subcategoria` | Nível 3, pertence a uma Categoria | 1 Subcategoria → N Documentos |
| `Documento` | Entidade principal — metadados completos do upload | **Decisão atual:** classificado em 1 única Subcategoria (pendente de confirmação — ver Seção 6); N:N com Area |
| `Documento_Area` | Associativa N:N, com `is_principal` | resolve "documento em mais de uma área", com área responsável marcada |

### 1.2 Metadados do documento (R2P3 — 16 campos do upload)

Não são necessariamente 16 colunas: vários viram FK/relacionamento em vez de coluna solta.

| Campo do cliente | Representação no modelo | Obrigatório? |
|---|---|---|
| Título | `documento.titulo` | Sim |
| Identificador/código | `documento.identificador_codigo` | Sim |
| Tipo de documento | FK `tipo_documento_id` (9 tipos definidos pelo cliente) | Sim |
| Número/revisão | `documento.numero_revisao` | Sim |
| Data de emissão | `documento.data_emissao` | Sim |
| Data de vigência | `documento.data_vigencia` | Quando aplicável |
| Área responsável | área marcada `is_principal=TRUE` em `documento_area` | Sim (garantido por trigger, ver 2.4) |
| Categoria e subcategoria | FK `subcategoria_id` (cascata até categoria/área) | Sim |
| Status (vigente/substituído/cancelado/em revisão) | FK `status` → `status_documento` | Sim |
| Nível de sigilo | FK `nivel_sigilo_id` (4 níveis) | Sim |
| Origem/fonte | `documento.origem_fonte` | Sim |
| Responsável pelo documento | FK `responsavel_id` → `usuario` | Sim |
| Idioma | FK `idioma_codigo` (lookup, não enum) | Sim |
| Norma/documento relacionado | tabela `documento_relacao` (N:N autorreferenciada) | Quando aplicável |
| Data de validade | `documento.data_validade` | Quando aplicável |
| Arquivo original | `documento.arquivo_original_url` | Sim |

**Papéis de usuário separados** (R1P7 distingue quem faz upload de quem aprova):
- `usuario_upload_id` — quem fez o upload (pode sugerir classificação inicial, mas não define acesso elevado sozinho)
- `responsavel_id` — dono/responsável pelo documento
- `aprovador_id` — quem validou/aprovou antes da disponibilização (nullable até a aprovação acontecer)

### 1.3 Por que `status_documento` e `status_workflow` são tabelas separadas

São conceitos diferentes, ambos confirmados pela AKAER:
- **`status_documento`** (R2P3): vigência do documento em si — `VIGENTE` / `SUBSTITUIDO` / `CANCELADO` / `EM_REVISAO`.
- **`status_workflow`** (R1P7): etapa do processo de publicação — `UPLOAD` → `CLASSIFICACAO_INICIAL` → `AGUARDANDO_APROVACAO` → `APROVADO`/`REJEITADO` → `DISPONIBILIZADO`.

Um documento pode estar `status = VIGENTE` e `status_workflow = DISPONIBILIZADO` simultaneamente, sem conflito — são eixos independentes.

### 1.4 Autorização de IA — separada do nível de sigilo (R2P6)

A AKAER foi explícita: autorização de IA **não é consequência automática** do sigilo. Por isso `politica_autorizacao_ia_sigilo` usa um status semântico próprio, e não um boolean derivado do sigilo:

| Sigilo | Status da política |
|---|---|
| Público | `PERMITIDO` |
| Interno | `CONFORME_POLITICA` |
| Restrito | `REQUER_AUTORIZACAO` |
| Confidencial | `BLOQUEADO` |

Isso é só a política **padrão**. Para documentos cuja política é `REQUER_AUTORIZACAO`, o processamento depende de um registro vigente em `documento_autorizacao_ia` com `autorizado = TRUE`. O comportamento para `PERMITIDO` e `CONFORME_POLITICA` — se dispensam totalmente o registro ou não — permanece pendente de definição (ver Seção 6). Para `BLOQUEADO`, a AKAER só confirmou "bloqueado por padrão"; não há confirmação de como (ou se) uma exceção funcionaria, então não presumimos um mecanismo de liberação.

> **Ambiguidade não resolvida (ver Seção 6):** não está claro se `PERMITIDO`/`CONFORME_POLITICA` dispensam totalmente o registro em `documento_autorizacao_ia`, ou se todo processamento — mesmo de documento Público — exige uma decisão registrada. Optamos por não decidir isso por conta própria.

> Evitamos assumir que "BLOQUEADO" significa "pode ser liberado mediante autorização explícita" — o cliente não confirmou isso. `documento_autorizacao_ia` registra o que de fato foi decidido, seja qual for a política.

### 1.5 Controle de acesso (R1P6 + R2P5)

Regra confirmada pelo cliente: **nunca herança automática por setor**. Acesso é o cruzamento de usuário + área + nível de sigilo máximo:

- `usuario_permissao_area` — regra geral: usuário X pode acessar, na área Y, documentos até o sigilo Z.
- `documento_restricao_especifica` — exceção pontual (`PERMITIR`/`NEGAR`) por usuário e documento, que sobrepõe a regra geral.

### 1.6 Relações entre documentos (R2P2)

Tabela `documento_relacao` (N:N autorreferenciada), com tipos `COMPLEMENTA` / `ALTERA` / `REFERENCIA` / `SUBSTITUI`. Preferida a um único campo `documento_relacionado_id` porque um documento pode se relacionar com vários outros, e o tipo da relação importa.

### 1.7 Roteamento de solicitações (R2P8)

`area.eh_governanca_central` marca a área que recebe pedidos sem categoria/responsável identificável. `solicitacao_documento.area_destino_id` é resolvida dinamicamente pela subcategoria pesquisada; cai na área de governança central só como fallback.

### 1.8 Login / autenticação

O backend já prevê JWT (`JWT_SECRET` no `.env.example`), que é **stateless** por natureza — por isso não existe tabela de sessão. O que o banco guarda é só a credencial e uma proteção básica contra força bruta, em `usuario`:
- `senha_hash` — hash da senha (bcrypt/argon2 na aplicação; nunca texto puro, nunca gerado via SQL)
- `ultimo_login_em`, `tentativas_login_falhas`, `bloqueado_ate` — suporte a lockout temporário após N tentativas falhas (a regra de quantas tentativas/por quanto tempo fica na aplicação, não no banco)

Não criamos tabela de refresh token/revogação agora — não foi pedido, e adicionar isso por conta própria seria inventar escopo. Se a aplicação precisar de "logout em todos os dispositivos" antes do JWT expirar, essa tabela entra depois, como evolução pontual.

## 2. Modelagem física (PostgreSQL)

Ver `../postgres/migrations/`. O schema foi dividido em 7 migrations numeradas por domínio (extensões/lookups, usuário/autenticação, taxonomia, documento, documento_area/relações, segurança/IA, solicitação) em vez de um único arquivo — cada uma roda de forma independente, na ordem numérica, e as dependências entre elas seguem essa mesma ordem. Decisões físicas:

- **UUID como PK** (`gen_random_uuid()`).
- **`conteudo` é nullable**, não `NOT NULL` — não está na lista de campos obrigatórios do cliente; é o texto extraído via OCR (R1P2), preenchido depois do upload, de forma assíncrona.
- **Obrigatoriedade em duas camadas**: `NOT NULL` para presença + `CHECK (btrim(...) <> '')` para impedir string vazia, nos campos textuais obrigatórios.
- **Lookups em vez de `ENUM`** (`tipo_documento`, `nivel_sigilo`, `status_documento`, `idioma`, etc.) — permitem adicionar valores sem migração de schema (`ALTER TYPE`), relevante porque a AKAER já corrigiu o número de níveis de sigilo uma vez (5→4) e a taxonomia de categorias ainda nem foi fechada (R2P1).
- **`documento_autorizacao_ia.vigente`**: como pode existir histórico de decisões (autorizado → revogado → autorizado de novo), esse campo + índice único parcial identificam qual é a decisão atual.
- **`trg_autorizacao_ia_versao_atual`**: garante que a autorização vigente corresponde à `numero_revisao` atual do documento — evita uma autorização antiga (Rev. A) cobrindo silenciosamente uma revisão nova (Rev. B) sem nova decisão. Isso cobre o caminho "criar/editar autorização"; o caminho complementar — **editar o documento** para uma nova revisão — é coberto por `trg_revogar_autorizacao_ia_em_nova_revisao`, que revoga (`vigente=FALSE`) a autorização antiga automaticamente quando `documento.numero_revisao` muda, obrigando uma nova decisão explícita para a revisão nova.
- **Dois triggers para "área principal"**, não um: `trg_documento_area_principal` (em `documento_area`) impede duas principais e impede zero principais *quando já existe alguma área vinculada*; mas isso sozinho não impede um documento sem **nenhuma** linha em `documento_area`. `trg_documento_tem_area_principal` (em `documento`) fecha esse caso, exigindo que todo documento tenha exatamente uma área principal ao fim da transação. Por isso o `seed.sql` insere documento + áreas dentro de um `BEGIN...COMMIT` explícito (validado rodando contra um Postgres real, não só revisado por leitura). O trigger de `documento_area` também verifica se o documento pai ainda existe antes de exigir área principal — isso evita que um `DELETE` em cascata (excluir o documento inteiro, que remove suas linhas de `documento_area` via `ON DELETE CASCADE`) seja bloqueado incorretamente pela própria exclusão.
- **`ck_documento_aprovacao_completa`**: `CHECK` simples impedindo `status_workflow` chegar a `APROVADO`/`DISPONIBILIZADO` sem `aprovador_id` e `data_aprovacao` preenchidos. Não modelamos a sequência completa do workflow (Upload→Classificação→Aprovação→Disponibilização) como máquina de estados no banco — isso fica na aplicação, por ser uma regra comportamental mais complexa que triggers tornariam frágil.
- **Trigger `trg_documento_touch`**: atualiza `atualizado_em` a cada `UPDATE`.

## 3. Modelagem NoSQL (MongoDB) — logs

A IA roda **em CPU** (modelo leve), o que reforça um log barato de escrever, sem infraestrutura dedicada.

| Coleção | Quem gera o evento | Pergunta que responde |
|---|---|---|
| `log_acesso` | Humano | Quem visualizou/baixou um documento, e quando? |
| `log_manipulacao` | Humano | Quem criou/editou/excluiu um documento, e o quê mudou? |
| `log_execucao_ia` | Sistema/IA | Quem acionou a IA, quando, sobre qual documento, e o que ela sugeriu? |

`log_execucao_ia` tem um campo `revisao` (status/usuario_revisor_id/subcategoria_final_id) que registra se a sugestão da IA foi aceita ou corrigida por um humano — mas **se essa revisão é obrigatória antes de valer, ainda é uma decisão pendente** (ver Seção 6). A estrutura já suporta os dois cenários sem mudança.

Também tem um campo `autorizacao_ia` (opcional), que liga o evento — o que de fato aconteceu, registrado no Mongo — à decisão que permitiu ele acontecer (`documento_autorizacao_ia.id` + `versao_autorizada`, no Postgres). Sem essa referência, não dava pra provar a partir do log sozinho que uma execução foi de fato autorizada; seria preciso cruzar os dois bancos manualmente. É opcional porque a obrigatoriedade de registro para `PERMITIDO`/`CONFORME_POLITICA` ainda está pendente.

Ver `mongo-schemas.js` para os validators `$jsonSchema` completos.

## 4. Validação do critério "documento sem campos obrigatórios não é salvo"

```sql
-- Falha: titulo NULL
INSERT INTO documento (identificador_codigo, tipo_documento_id, numero_revisao, data_emissao,
    subcategoria_id, status, nivel_sigilo_id, origem_fonte, responsavel_id, idioma_codigo,
    arquivo_original_url, usuario_upload_id)
VALUES ('DOC-001', (SELECT id FROM tipo_documento LIMIT 1), 'Rev. A', '2026-01-01',
    (SELECT id FROM subcategoria LIMIT 1), 'VIGENTE', (SELECT id FROM nivel_sigilo LIMIT 1),
    'fonte', (SELECT id FROM usuario LIMIT 1), 'pt', 'https://...', (SELECT id FROM usuario LIMIT 1));
-- ERROR: null value in column "titulo" violates not-null constraint

-- Falha: tipo_documento_id inexistente
-- ERROR: violates foreign key constraint "documento_tipo_documento_id_fkey"

-- Falha: status inválido (fora de VIGENTE/SUBSTITUIDO/CANCELADO/EM_REVISAO)
-- ERROR: violates foreign key constraint na tabela status_documento

-- Falha: nivel_sigilo_id inexistente (não é texto livre "SIGILOSO" — não existe mais)
-- ERROR: violates foreign key constraint na tabela nivel_sigilo

-- Falha: documento com duas áreas principais
INSERT INTO documento_area (documento_id, area_id, is_principal) VALUES (:doc_id, :area_a, TRUE);
INSERT INTO documento_area (documento_id, area_id, is_principal) VALUES (:doc_id, :area_b, TRUE);
-- ERROR: duplicate key violates unique constraint "uq_documento_area_principal"

-- Falha: documento sem NENHUMA área vinculada (o caso que o segundo trigger fecha)
BEGIN;
INSERT INTO documento (...) VALUES (...); -- sem nenhum INSERT em documento_area depois
COMMIT;
-- ERROR (no COMMIT): Documento ... precisa ter exatamente uma área principal vinculada

-- Falha: documento com área vinculada mas nenhuma marcada como principal
INSERT INTO documento_area (documento_id, area_id, is_principal) VALUES (:doc_id, :area_a, FALSE);
-- ERROR (no commit): Documento ... deve ter exatamente uma área principal (encontradas: 0)

-- Falha: relação de documento consigo mesmo
INSERT INTO documento_relacao (documento_origem_id, documento_destino_id, tipo_relacao)
VALUES (:doc_id, :doc_id, 'COMPLEMENTA');
-- ERROR: violates check constraint "ck_documento_relacao_nao_auto"

-- Deve FUNCIONAR: excluir um documento inteiro (com área principal já
-- cadastrada) não deve ser bloqueado pelo trigger de área principal,
-- mesmo com o DELETE CASCADE removendo as linhas de documento_area.
DELETE FROM documento WHERE id = :doc_id;
-- OK (sem erro)

-- Autorização acompanha mudança de revisão: ao alterar numero_revisao,
-- a autorização vigente antiga é revogada automaticamente.
-- 1) documento Rev. A com autorização vigente para Rev. A
-- 2) UPDATE documento SET numero_revisao = 'Rev. B' WHERE id = :doc_id;
-- 3) a autorização antiga passa a ter vigente = FALSE automaticamente
SELECT vigente FROM documento_autorizacao_ia WHERE documento_id = :doc_id;
-- esperado: FALSE (nenhuma autorização vigente para a Rev. B ainda —
-- precisa de uma nova decisão explícita antes de processar por IA)
```

## 5. Ordem de execução

1. `../postgres/migrations/*.sql`, em ordem numérica (001 a 007) — extensões, lookups, usuário/autenticação, taxonomia, `documento` completo, controle de acesso, autorização de IA, triggers.
2. `../postgres/seed.sql` — estrutura de 3 níveis (4 áreas), usuários, e um documento de exemplo completo (cenário de compatibilidade de materiais, indicado pela AKAER como caso de validação).

Ambos os passos foram executados de ponta a ponta contra um PostgreSQL 16 real antes desta entrega — não é só uma revisão de leitura do SQL.

O `erd-fisico.png` (nesta mesma pasta) foi gerado no Redgate Data Modeler a partir deste schema completo, com notação pé-de-galinha para as cardinalidades. O modelo lógico não tem um diagrama visual separado nesta entrega — está representado textualmente ao longo desta Seção 1 (entidades, relacionamentos e o porquê de cada decisão), agrupado nos mesmos domínios: Cadastro & Taxonomia (1.1), Documento (1.2), Segurança/IA e Controle de Acesso (1.4-1.5), Relacionamento & Solicitação (1.6-1.7).

## 6. Decisões pendentes (não resolvidas por hipótese)

Estes pontos aparecem na modelagem de forma que **não impede** o funcionamento atual, mas que pode mudar quando houver resposta do cliente/PO:

| Tema | Estado atual | O que falta |
|---|---|---|
| Documento em múltiplas subcategorias | Hoje é 1:N (`documento.subcategoria_id`) | Se vier resposta "sim, N:N", vira tabela `documento_subcategoria`, análoga a `documento_area` |
| Revisão humana obrigatória da classificação da IA | `log_execucao_ia.revisao` existe mas pode ficar `PENDENTE` indefinidamente | Confirmar se é bloqueante antes de gravar como definitivo |
| Retenção de `log_acesso` | Sem TTL/expiração definida | Confirmar prazo — no contexto AS9100D pode ser tratado como registro de qualidade, não só log técnico descartável |
| Estratégia de grupos de usuário | Só existe permissão por usuário individual (`usuario_permissao_area`) | Cliente mencionou "usuário **ou grupo**" — modelo de grupo ainda não definido |
| Reprocessamento de embeddings ao trocar de modelo | `log_execucao_ia.versao_modelo` já dá rastreabilidade | Regra de reindexação completa é decisão técnica do time, não veio da AKAER — não vira requisito formal ainda |
| `PERMITIDO`/`CONFORME_POLITICA` dispensa registro em `documento_autorizacao_ia`? | Hoje só exigimos registro explícito para `REQUER_AUTORIZACAO`/`BLOQUEADO` | O cliente não esclareceu se documento Público/Interno também precisa de uma decisão registrada, ou se a política padrão já basta sem registro |

## 7. Mapeamento com os critérios de aceite da AKA-14

| Critério | Onde é atendido |
|---|---|
| Tabelas documento, area, categoria e subcategoria criadas | `postgres/migrations/003_taxonomia.sql`, `004_documento.sql` |
| Documento ligado a mais de uma área | `documento_area` (N:N) + `is_principal` (`005_documento_area_e_relacoes.sql`) |
| Estrutura de 3 níveis com dados iniciais | `postgres/seed.sql` |
| Documento sem campos obrigatórios não é salvo | `NOT NULL`/`CHECK`/FK em `documento`, testes na Seção 4 |
