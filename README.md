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

1. Crie uma branch a partir de `develop`: `feature/nome-da-tarefa`
2. Commits e título do PR seguem [Conventional Commits](https://www.conventionalcommits.org), em inglês: `feat:`, `fix:`, `docs:`, `chore:`, `test:`
3. Abra o PR contra `develop` — exige 1 aprovação, CI verde e título validado
4. Merge sempre via **Squash and merge**

## CI

O workflow `Backend CI` roda em todo PR/push para `main` e `develop`: instala dependências e executa lint (`ruff`).

## Links

- Repositório agregador: [API-6SEM](https://github.com/DenariusData/API-6SEM)
- Frontend: [API-6SEM-FRONTEND](https://github.com/DenariusData/API-6SEM-FRONTEND)
