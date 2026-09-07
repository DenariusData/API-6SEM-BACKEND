# API-6SEM-BACKEND

Backend do **AkaVision** — Classificador de Documentos Técnicos, projeto integrado Fatec SJC × AKAER.

## Stack

- **Linguagem:** Python
- **Modelo de IA/embeddings:** local, sem chamadas a serviços externos (restrição do projeto)
- _Framework web e banco de dados a confirmar com o time e documentar aqui assim que definidos_

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

# 5. Rode a aplicação
# (comando a definir assim que o framework for escolhido)
```

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
