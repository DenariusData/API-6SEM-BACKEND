## Descrição

<!-- O que esse PR faz? Por quê? -->

## Issue/Tarefa relacionada

<!-- Link do Jira / número da issue -->
Jira:

## Requisito relacionado

<!-- Marque o(s) requisito(s) da Matriz de Rastreabilidade (Confluence) que este PR implementa. Pode marcar mais de um. -->

**E1 — Ingestão e Processamento Documental**
- [ ] RF01 — Estrutura de dados conforme D4 e D7
- [ ] RF02 — Upload de documento com metadados
- [ ] RF03 — Extração de texto preservando a estrutura
- [ ] RF05 — Divisão em chunks e indexação
- [ ] RF06 — Identificar a revisão vigente
- [ ] RF07 — Solicitar acesso ou inclusão de documento

**E2 — Classificação e Controle de Acesso**
- [ ] RF08 — Gerenciar usuários e perfis
- [ ] RF09 — Classificar por nível de sigilo
- [ ] RF10 — Validar a classificação antes de disponibilizar
- [ ] RF11 — Acesso por usuário/grupo, área e classificação
- [ ] RF12 — Filtrar trechos pela permissão antes da IA

**E3 — Consulta com IA**
- [ ] RF13 — Pergunta em linguagem natural com resposta fundamentada
- [ ] RF14 — Perguntar em português sobre documentos em inglês
- [ ] RF15 — Explicitar a relação entre documentos diferentes
- [ ] RF16 — Abrir o documento no trecho citado

**E4 — Infraestrutura e Base de Testes**
- [ ] RF17 — LLM e embeddings em infraestrutura local
- [ ] RF18 — Base de testes representativa carregada

**E5 — Privacidade e LGPD**
- [ ] RF19 — Dados pessoais em banco separado
- [ ] RF20 — Termo de Uso, Aviso de Privacidade e Consentimento
- [ ] RF21 — Log imutável de acessos e ações
- [ ] RF24 — Tela Meus dados
- [ ] RF27 — Exportar os dados pessoais
- [ ] RF28 — Alertas de login anômalo ou download em massa
- [ ] RF29 — Consulta do log de auditoria com exportação

**Não funcionais e restrições**
- [ ] RNF02 — Modelagem de dados / banco de dados
- [ ] RNF03 — Anonimização (LGPD)
- [ ] RNF04 — Logs de acesso e manipulação
- [ ] D15 — Desempenho

- [ ] Não se aplica (chore/CI/infra sem requisito direto)

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

## Checklist

- [ ] Testei localmente antes de abrir o PR
- [ ] Título do PR segue o padrão (`feat: ...`, `fix: ...`, em inglês)
- [ ] Não deixei `console.log`/`print` de debug esquecidos
- [ ] Atualizei o `.env.example` se adicionei variável de ambiente nova
- [ ] Não há conflitos com a branch de destino
