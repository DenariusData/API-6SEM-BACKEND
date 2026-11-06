# API-6SEM-BACKEND

Backend do **AkaVision** — Classificador de Documentos Técnicos, projeto integrado Fatec SJC × AKAER.

## Stack

- **Linguagem:** Python
- **Modelos de IA:** locais via Ollama (bge-m3 para embeddings, Llama 3.2 para respostas), sem chamadas a serviços externos (restrição do projeto)
- **Banco de dados:** PostgreSQL com pgvector, via Docker Compose
- _Framework web a confirmar com o time e documentar aqui assim que definido_

## Como rodar localmente

```bash
# 1. Clone o repositório (ou entre na pasta, se já estiver como submódulo do API-6SEM)
git clone https://github.com/DenariusData/API-6SEM-BACKEND.git
cd API-6SEM-BACKEND

# 2. Crie e ative um ambiente virtual
python -m venv .venv
source .venv/bin/activate      # Linux/Mac
.venv\Scripts\activate         # Windows

# 3. Instale as dependências
pip install -r requirements.txt

# 4. Configure as variáveis de ambiente
cp .env.example .env
# edite o .env com os valores da sua máquina

# 5. Suba o banco de dados
docker compose up -d

# 6. Rode a aplicação
# (comando a definir assim que o framework for escolhido)
```

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

## CI

O workflow `Backend CI` roda em todo PR/push para `main` e `develop`: instala dependências e executa lint (`ruff`).

## Links

- Repositório agregador: [API-6SEM](https://github.com/DenariusData/API-6SEM)
- Frontend: [API-6SEM-FRONTEND](https://github.com/DenariusData/API-6SEM-FRONTEND)
