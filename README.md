# API-6SEM-BACKEND

Backend do **AkaVision** — Classificador de Documentos Técnicos, projeto integrado Fatec SJC × AKAER.

## Stack

- **Linguagem:** Python 3.12
- **Framework web:** Django 5.2 (API REST com JSON e autenticação JWT)
- **Bancos de dados:** dois PostgreSQL via Docker Compose: o de documentos com **pgvector** (busca vetorial) e o de credenciais, separado por LGPD
- **Modelos de IA:** locais via Ollama (bge-m3 para embeddings, Llama 3.2 para respostas), sem chamadas a serviços externos (restrição do projeto)
- **Qualidade:** pytest (testes unitários e de integração), Ruff (lint e formatação), pre-commit, GitHub Actions e SonarQube Cloud

## Como rodar localmente

Resumo abaixo. O passo a passo completo, com o frontend, a IA local e os problemas comuns, está no [Manual de Instalação](https://github.com/DenariusData/API-6SEM/blob/develop/docs/manual-instalacao.md).

```bash
# 1. Ambiente virtual e dependências
python -m venv venv
.\venv\Scripts\Activate.ps1      # Windows (PowerShell)
source venv/bin/activate          # Linux/Mac
pip install -r requirements-dev.txt

# 2. Variáveis de ambiente: copie e preencha (bancos, JWT, Ollama)
cp .env.example .env              # Windows: copy .env.example .env

# 3. Bancos (com o Docker Desktop aberto)
docker compose up -d

# 4. Tabelas e dados iniciais
python manage.py migrate
python manage.py migrate --database credenciais_db
python manage.py seed_taxonomia
python manage.py seed_usuarios_demo

# 5. Rodar
python manage.py runserver        # http://localhost:8000
```

Testes: `pytest tests` (com os bancos no ar). Rotas da API: [Documentação da API](https://github.com/DenariusData/API-6SEM/blob/develop/docs/api.md).

## Pipeline RAG (EverySpec)

Scripts que baixam as especificações do EverySpec, preparam os textos, geram os embeddings e respondem perguntas citando documento e página.

| Etapa | Script |
|---|---|
| Download dos PDFs | `baixar_everyspec.py` |
| Extração do texto por página | `extrair_texto.py` |
| Divisão em trechos e filtro de qualidade | `gerar_chunks.py` |
| Geração dos embeddings pelo Ollama local | `gerar_embeddings.py` |
| Importação dos embeddings gerados no Colab (`.npz`) | `importar_embeddings.py` |
| Busca vetorial | `buscar.py` |
| Resposta com citação das fontes | `responder.py` |

O passo a passo completo está em [docs/pipeline-rag.md](docs/pipeline-rag.md).

PDFs, textos extraídos, trechos e o arquivo `.npz` ficam fora do Git. O `.npz` é compartilhado pelo Google Drive.

## Fluxo de contribuição

1. Instale os hooks uma vez por clone (validam nome da branch, mensagem de commit e lint):
   ```bash
   pip install pre-commit
   pre-commit install --hook-type pre-commit --hook-type commit-msg
   ```
2. Crie a branch a partir de `develop` com o código da task do Jira: `<tipo>/AKA-<número>-descricao-curta` (ex.: `feature/AKA-55-ai-response-language-support`)
3. Commits em inglês no formato `<tipo>: AKA-<número> <descrição>` (ex.: `feat: AKA-95 add script to test bge-m3 locally`)
4. Abra o PR contra `develop` com o título no mesmo formato, descrição começando com maiúscula (ex.: `feat: AKA-55 Add language support for AI response`), e preencha o template
5. Para o merge: CI verde, 1 aprovação e título validado. Merge sempre via **Squash and merge**

Padrão completo (tipos aceitos, exemplos e regras): [Project Guidelines no API-6SEM](https://github.com/DenariusData/API-6SEM#project-guidelines).

## Testes

O modelo de testes unitários (o que é a unidade, o que testar, quando, quem confere, templates e mapa dos testes) está no [README do projeto](https://github.com/DenariusData/API-6SEM#tests). Aqui a unidade é a **função Python** com regra de negócio, chamada direto, com banco e IA mockados. Todo PR preenche o **Plano de testes** do template.

| | Comando |
|---|---|
| Testes unitários (`tests/unit`) | `pytest tests/unit` |
| Testes de integração (`tests/integration`, com PostgreSQL) | `pytest tests/integration` |
| Cobertura dos unitários | `pytest tests/unit --cov --cov-report=term-missing` |

## CI

O workflow `Backend CI` roda em todo PR e push para `main` e `develop`: lint (`ruff`), testes unitários, testes de integração com PostgreSQL e análise no SonarQube Cloud (cobertura e qualidade).

## Links

- Repositório agregador: [API-6SEM](https://github.com/DenariusData/API-6SEM)
- Frontend: [API-6SEM-FRONTEND](https://github.com/DenariusData/API-6SEM-FRONTEND)
