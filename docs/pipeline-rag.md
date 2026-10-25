# RAG com documentos do EverySpec

Pipeline de RAG (Retrieval-Augmented Generation) que responde perguntas em português com base em normas e especificações técnicas públicas do [EverySpec](https://everyspec.com), citando documento e página.

Os dados (PDFs, textos, chunks e embeddings) **não ficam no repositório**. Cada etapa é recriada pelos scripts abaixo, ou a base pronta pode ser importada de um arquivo exportado (veja "Compartilhar a base pronta").

## Visão geral

| Etapa | Script | Resultado |
|---|---|---|
| 1. Baixar PDFs | `baixar_everyspec.py` | pasta `pdfs_everyspec/` |
| 2. Extrair texto | `extrair_texto.py` | pasta `textos_extraidos/` |
| 3. Dividir em chunks | `gerar_chunks.py` | `chunks/chunks.jsonl` |
| 4. Gerar embeddings | `gerar_embeddings.py`, ou Colab + `importar_embeddings.py` | tabela `rag_chunks` no PostgreSQL |
| 5. Buscar trechos | `buscar.py` | trechos mais parecidos com a pergunta |
| 6. Responder | `responder.py` | resposta em português com as fontes |

Stack: Python, PostgreSQL com pgvector (via Docker) e Ollama, com o modelo `bge-m3` para embeddings e o `llama3.2` para as respostas.

## Pré-requisitos

- **Python 3.10 ou mais recente.** No Windows, marque "Add python.exe to PATH" na instalação.
- **[Docker Desktop](https://www.docker.com/products/docker-desktop/).** Precisa da virtualização ativada na BIOS e do WSL 2.
- **[Ollama](https://ollama.com/download).**

Dependências Python:

    python -m pip install requests beautifulsoup4 pymupdf wordfreq "psycopg[binary]" numpy

Modelos do Ollama:

    ollama pull bge-m3
    ollama pull llama3.2

Banco de dados (PostgreSQL com pgvector):

    docker compose up -d

Depois de reiniciar o computador, abra o Docker Desktop e rode `docker compose up -d` de novo. Os dados ficam salvos no volume `rag_pgdata`.

## 1. Baixar os PDFs

Teste rápido (3 documentos):

    python baixar_everyspec.py --categorias FED-STD --max-por-categoria 3

Amostra padrão (FED-STD, MIL-HDBK e NASA, 20 documentos de cada, de 5 a 10 minutos):

    python baixar_everyspec.py

| Opção | Padrão | O que faz |
|---|---|---|
| `--categorias` | `FED-STD MIL-HDBK NASA` | Categorias do site, com o nome como aparece na URL (ex.: `MIL-STD`, `FAA`, `DOE`) |
| `--max-por-categoria` | `20` | Quantos PDFs novos baixar por categoria |
| `--pasta` | `pdfs_everyspec` | Pasta onde os PDFs são salvos |
| `--atraso` | `2.0` | Segundos entre requisições. Não diminua muito, para não sobrecarregar o site |
| `--tamanho-max-mb` | `30` | Pula arquivos maiores que esse tamanho |
| `--incluir-adendos` | desligado | Baixa também notices e amendments, que costumam ter só 1 a 3 páginas |

Dá para interromper com Ctrl+C e rodar de novo: o script continua de onde parou, usando o `pdfs_everyspec/manifesto.jsonl`.

## 2. Extrair o texto

    python extrair_texto.py

Lê cada PDF página por página, remove a marca d'água "Downloaded from everyspec.com" e gera `textos_extraidos/relatorio_extracao.csv`. PDFs em que mais de 30% das páginas não têm texto são marcados como **PRECISA OCR**.

## 3. Dividir em chunks

    python gerar_chunks.py --excluir MIL_HDBK_103 MIL-HDBK-103 MIL-HDBK-17-1E MIL-HDBK-17-2E MIL-HDBK-17-3E MIL-HDBK-17B CxP_70000.031897 CxP_70007_RevB_Change001

O script:

- usa só as páginas com texto;
- remove pontilhados de sumário e junta palavras quebradas por hífen;
- agrupa o texto em pedaços de até 1500 caracteres (cerca de 660 tokens no bge-m3), repetindo 200 caracteres entre um pedaço e o seguinte;
- descarta chunks com menos de 80% de palavras reconhecidas em inglês (OCR ruim) e salva esses chunks em `chunks/descartados.jsonl`.

Por que excluir esses documentos:

- **MIL-HDBK-103** (todas as revisões) é uma lista de códigos de microcircuitos. Sozinho, ocupava metade da base e poluía a busca.
- **Revisões antigas** (MIL-HDBK-17 E/B, CxP_70000 original, CxP_70007 Change001) duplicam o conteúdo das revisões mais novas.

## 4. Gerar os embeddings

Cada chunk vira um vetor de 1024 dimensões com o `bge-m3` e é gravado na tabela `rag_chunks`.

### Opção A: na própria máquina (precisa de uma boa placa de vídeo)

    python gerar_embeddings.py --limite 50
    python gerar_embeddings.py

Dá para interromper e continuar. Chunks que falharem no Ollama ficam em `chunks/falhas_embedding.jsonl`. Numa GTX 1050 com 2 GB, a velocidade é de cerca de 1 chunk por segundo (várias horas); nesse caso use a opção B.

### Opção B: no Google Colab (GPU gratuita)

1. Em https://colab.research.google.com, crie um notebook e escolha **Ambiente de execução > Alterar o tipo de ambiente de execução > GPU T4**.
2. Rode cada bloco abaixo numa célula separada.

Célula 1, conferir a GPU:

    !nvidia-smi

Célula 2, enviar o `chunks/chunks.jsonl`:

    from google.colab import files
    enviado = files.upload()

Célula 3, gerar os embeddings:

    !pip install -q -U sentence-transformers
    import json, time
    import numpy as np
    from sentence_transformers import SentenceTransformer

    chunks = [json.loads(linha) for linha in open("chunks.jsonl", encoding="utf-8") if linha.strip()]
    ids = [c["id"] for c in chunks]
    textos = [c["texto"] for c in chunks]

    modelo = SentenceTransformer("BAAI/bge-m3", device="cuda")
    modelo.half()
    modelo.max_seq_length = 1024

    inicio = time.time()
    vetores = modelo.encode(textos, batch_size=32, normalize_embeddings=True,
                            show_progress_bar=True, convert_to_numpy=True)
    print(f"{vetores.shape[0]} vetores em {(time.time() - inicio) / 60:.1f} min")

Célula 4, salvar e baixar:

    np.savez("embeddings_bge_m3.npz", ids=np.array(ids), vetores=vetores.astype(np.float32))
    files.download("embeddings_bge_m3.npz")

3. Coloque o `embeddings_bge_m3.npz` na pasta do projeto e importe:

    python importar_embeddings.py embeddings_bge_m3.npz

Antes de gravar, o script compara amostras dos vetores do Colab com os do Ollama local e só importa se a similaridade for de pelo menos 0.98. Isso garante que as perguntas, convertidas pelo Ollama, fiquem no mesmo espaço dos chunks.

## 5. Testar a busca

    python buscar.py "Quanto tempo a Orion deve manter a tripulação viva sem pressurização?"

Mostra os 5 trechos mais parecidos, com documento, páginas e similaridade. Para pergunta em português e documento em inglês, similaridades entre 0.6 e 0.75 são normais.

## 6. Responder perguntas

    python responder.py "Quanto tempo a Orion deve manter a tripulação viva sem pressurização?" --k 3 --mostrar-trechos

Busca os trechos, envia ao `llama3.2` com a instrução de responder em português, usar só os trechos e citar as fontes como [1], [2]. A resposta aparece aos poucos e, no final, a lista de fontes.

| Opção | Padrão | O que faz |
|---|---|---|
| `--k` | `5` | Quantos trechos enviar ao LLM. Use `3` em PCs com pouca memória |
| `--modelo` | `llama3.2` | LLM do Ollama. Em PCs com pouca memória de vídeo, use `llama3.2:1b` |
| `--mostrar-trechos` | desligado | Mostra os trechos enviados, para conferir a resposta |

Antes de a resposta começar, o modelo precisa ler os trechos. Em máquinas modestas isso pode levar alguns minutos.

## 7. Endpoint da tela de pergunta

A API faz o mesmo que o `responder.py`, com o mesmo prompt e as mesmas citações:

    POST /api/perguntas/
    Authorization: Bearer <token do /api/auth/login/>
    {"pergunta": "Quais os requisitos de fatores humanos para a tripulação?"}

Resposta:

    {
      "pergunta": "Quais os requisitos de fatores humanos para a tripulação?",
      "resposta": "... [1]",
      "encontrou": true,
      "fontes": [
        {"numero": 1, "documento_id": null, "documento": "CxP 70024", "revisao": "BASELINE", "pagina": "30",
         "pagina_inicio": 30, "pagina_fim": 30, "titulo": "CxP 70024 (BASELINE), ...",
         "url_pdf": "https://...", "similaridade": 0.568, "citada": true}
      ],
      "tempos_ms": {"busca": 420, "ia": 9800, "total": 10220}
    }

- **Sem base no acervo**: se nenhum trecho passar de `RAG_SIMILARIDADE_MINIMA`, a IA nem é chamada, e a resposta vem com `"encontrou": false`, `"fontes": []` e a mensagem "Não encontrei essa informação nos documentos consultados.". O mesmo acontece quando a própria IA diz que os trechos não têm a resposta.
- **`documento_id`**: id do documento do sistema, usado pelo botão "Ver documento" (AKA-61); `null` na base pública do EverySpec (seção 9).
- **Revisão**: nos documentos do sistema, é o número da revisão do upload. Na base pública, vem do título do EverySpec, por exemplo `(REV. B)`, `(BASELINE)` ou a letra final do código (`FED-STD-123H` fica com revisão `H`). É `null` quando o documento não indica revisão.
- **Desempenho**: toda pergunta respondida é gravada na tabela `registro_pergunta` com os tempos de busca, da IA e total. Se o Ollama ou o banco não responderem, a API devolve `503` e não grava nada.

| Variável | Padrão | O que faz |
|---|---|---|
| `RAG_MODELO_LLM` | `llama3.2` | LLM que escreve a resposta |
| `RAG_MODELO_EMBEDDING` | `bge-m3` | Modelo que converte a pergunta em vetor |
| `RAG_TOP_K` | `5` | Quantos trechos buscar |
| `RAG_SIMILARIDADE_MINIMA` | `0.5` | Trechos abaixo disso são descartados. No acervo atual, perguntas do domínio ficam acima de 0.55 e perguntas sem relação ficam entre 0.33 e 0.46 |

## 8. Processamento automático do documento aprovado

Documentos enviados pelo sistema não passam pelas etapas 1 a 4 à mão. Quando um documento é aprovado (`POST /api/documents/<id>/aprovar/`), o backend roda as mesmas etapas só para ele, numa thread, depois de responder a aprovação (`core_api/services/processamento.py`):

1. extrai o texto com a página (`extrair_texto.py`), lendo o PDF da pasta privada do upload;
2. divide em trechos e aplica o filtro de qualidade (`gerar_chunks.py`, mesmos padrões: 1500 caracteres, 200 de sobreposição, qualidade mínima de 80%). O filtro usa o idioma do documento (inglês ou português);
3. gera os embeddings com o `bge-m3` do Ollama local (`gerar_embeddings.py`) e grava os trechos em `chunk` e os vetores em `embedding_version`, ligados ao documento.

O estado fica no próprio documento, em `status_processamento` (`PENDENTE`, `PROCESSANDO`, `CONCLUIDO`, `ERRO`), com o motivo do erro em `erro_processamento`. Os dois aparecem no detalhe do documento. Para reprocessar (depois de um erro, ou se o servidor parou no meio):

    python manage.py reprocessar_documento <id do documento>

Não há chamada externa: o processamento recusa um `OLLAMA_URL` que não seja a própria máquina (mesma regra do teste do bge-m3, AKA-95) e nunca baixa arquivo de URL. Para aprovar sem processar (por exemplo, numa máquina sem Ollama), use `PROCESSAMENTO_AUTOMATICO=false` e rode o comando depois.

| Variável | Padrão | O que faz |
|---|---|---|
| `PROCESSAMENTO_AUTOMATICO` | `true` | Processa o documento aprovado em segundo plano |

## 9. O que a busca enxerga (AKA-51)

A tela de pergunta, o `buscar.py` e o `responder.py` usam a mesma busca (`core_api/services/rag/busca_vetorial.py`), que junta duas fontes e ordena pela similaridade:

| Fonte | Tabelas | Filtro | `documento_id` na fonte |
|---|---|---|---|
| Documentos do sistema | `chunk` + `embedding_version` (vigente) | só `Documento.objects.disponiveis()` e o modelo da pergunta | id do documento |
| Base pública do EverySpec | `rag_chunks` | só o modelo da pergunta (normas públicas) | `null` |

- Documento **aguardando validação** ou **devolvido** nunca aparece em resposta. O documento aprovado aparece assim que o processamento (seção 8) termina.
- O filtro fica só em `Documento.objects.disponiveis()`. A regra de permissão da Sprint 3 entra ali e a busca passa a respeitá-la sem outra mudança.
- O `documento_id` da fonte abre o documento no visualizador seguro do front (AKA-61). Na base pública ele é `null`, e a fonte continua com o `url_pdf` do EverySpec.
- **Modelo do embedding**: só entram vetores gerados pelo mesmo modelo da pergunta (`RAG_MODELO_EMBEDDING`, padrão `bge-m3`; nos scripts, `--modelo`/`--modelo-embedding`). Vetores de modelos diferentes não se comparam.
- **Banco da base pública**: os comandos `gerar_embeddings`/`importar_embeddings` gravam a `rag_chunks` pelo `DATABASE_URL`, e a busca lê pela conexão do Django. Os dois precisam apontar para o mesmo banco (o padrão de ambos é `localhost:5434/rag`). Se a tabela não existir nesse banco, a busca usa só os documentos do sistema e avisa no log (`Tabela rag_chunks não encontrada...`). Para conferir:

      python manage.py shell -c "from django.db import connection as c; cur=c.cursor(); cur.execute('select count(*) from rag_chunks'); print(cur.fetchone())"

- **Índice HNSW e filtro**: o Postgres percorre o índice pela distância e só depois filtra. Sem ajuste, o índice entrega só 40 vizinhos (`hnsw.ef_search`), e com muitos trechos de documentos não disponíveis perto da pergunta a busca podia voltar vazia. A busca liga `hnsw.iterative_scan = relaxed_order` (pgvector 0.8+, o do `docker-compose.yml`), pede 4×k candidatos de cada fonte e refaz a ordem. Em pgvector mais antigo, usa `hnsw.ef_search = 200` e avisa no log.
- **Desempenho** (20 mil trechos em cada fonte, vetores agrupados por assunto, índice HNSW): mediana ~145 ms, p95 ~190 ms, pior caso ~220 ms, com o mesmo resultado da busca exata (recall@5 = 100%). Sem contar o tempo do Ollama para converter a pergunta. A primeira busca depois de reiniciar o Postgres é mais lenta, porque o índice ainda está sendo lido do disco.

## Compartilhar a base pronta

Os embeddings só precisam ser gerados uma vez. Para levar a base a outro computador, exporte a tabela (no PowerShell, na máquina que já tem a base):

    docker exec rag-postgres pg_dump -U rag -d rag -t rag_chunks -Fc -f /tmp/rag_chunks.dump
    docker cp rag-postgres:/tmp/rag_chunks.dump ./rag_chunks.dump

Compartilhe o `rag_chunks.dump` pelo Google Drive. No outro computador, com o Docker e o Ollama (`bge-m3` e `llama3.2`) prontos:

    docker compose up -d
    docker exec rag-postgres psql -U rag -d rag -c "CREATE EXTENSION IF NOT EXISTS vector;"
    docker cp rag_chunks.dump rag-postgres:/tmp/rag_chunks.dump
    docker exec rag-postgres pg_restore -U rag -d rag --no-owner /tmp/rag_chunks.dump
    docker exec rag-postgres psql -U rag -d rag -c "SELECT count(*) FROM rag_chunks;"

Nesse caso não é preciso baixar os PDFs nem rodar as etapas 1 a 4.

## Limitações conhecidas

- **Tabelas perdem a estrutura** na extração: os valores viram uma lista solta. Perguntas sobre valores específicos de tabelas podem ter respostas fracas.
- **PDFs só com imagem** (digitalizados sem texto) ficam de fora, porque ainda não há OCR.
- **O `llama3.2` é lento** em placas de vídeo com 2 GB. O `llama3.2:1b` é mais rápido, mas escreve respostas mais simples.
- **As respostas ainda não foram avaliadas** com um conjunto de perguntas de teste.

## Problemas comuns

- **"Python was not found"**: reinstale o Python marcando "Add python.exe to PATH" e abra um terminal novo.
- **"can't open file"**: o terminal está em outra pasta. Use `cd` para entrar na pasta do projeto.
- **Comando não reconhecido logo depois de instalar algo** (ollama, docker): feche e abra o terminal (ou o VS Code inteiro).
- **Erros de
