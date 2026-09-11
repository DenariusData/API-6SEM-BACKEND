# Ingestão de documentos do EverySpec

Scripts que montam a base de documentos do RAG a partir do [EverySpec](https://everyspec.com), uma biblioteca pública de normas e especificações governamentais e militares dos EUA.

Os PDFs e textos gerados **não ficam no repositório**. Cada pessoa gera os dados na própria máquina rodando os scripts abaixo.

## Pré-requisitos

Python 3.10 ou mais recente. No Windows, marque **"Add python.exe to PATH"** na instalação.

Instale as dependências:

    python -m pip install requests beautifulsoup4 pymupdf

## 1. Baixar os PDFs

Rode os comandos a partir desta pasta.

Teste rápido (3 documentos):

    python baixar_everyspec.py --categorias FED-STD --max-por-categoria 3

Amostra padrão (FED-STD, MIL-HDBK e NASA, 20 documentos de cada, leva de 5 a 10 minutos):

    python baixar_everyspec.py

### Opções

| Opção | Padrão | O que faz |
|---|---|---|
| `--categorias` | `FED-STD MIL-HDBK NASA` | Categorias do site, com o nome como aparece na URL (ex.: `MIL-STD`, `FAA`, `DOE`, `FED_SPECS`) |
| `--max-por-categoria` | `20` | Quantos PDFs novos baixar por categoria |
| `--pasta` | `pdfs_everyspec` | Pasta onde os PDFs são salvos |
| `--atraso` | `2.0` | Segundos entre requisições. Não diminua muito, para não sobrecarregar o site |
| `--tamanho-max-mb` | `30` | Pula arquivos maiores que esse tamanho |
| `--incluir-adendos` | desligado | Baixa também notices e amendments, que costumam ter só 1 a 3 páginas |

### Arquivos gerados

    pdfs_everyspec/
    ├── FED-STD/
    │   └── FED-STD-3.011136.pdf
    ├── MIL-HDBK/
    ├── NASA/
    └── manifesto.jsonl    (título, categoria e link de origem de cada PDF)

Dá para interromper com Ctrl+C e rodar de novo: o script continua de onde parou e pula o que já está no `manifesto.jsonl`. Para baixar tudo do zero, apague a pasta `pdfs_everyspec`.

## 2. Extrair o texto dos PDFs

Depois de baixar os PDFs:

    python extrair_texto.py

O script lê cada PDF página por página, remove a marca d'água "Downloaded from everyspec.com" e verifica se o PDF tem texto ou é uma digitalização (imagem).

### Arquivos gerados

    textos_extraidos/
    ├── FED-STD/
    │   └── FED-STD-3.011136.json    (texto de cada página + metadados do manifesto)
    ├── MIL-HDBK/
    ├── NASA/
    └── relatorio_extracao.csv       (resumo de todos os PDFs, abre no Excel)

PDFs em que mais de 30% das páginas não têm texto são marcados como **PRECISA OCR** no relatório. A pasta `textos_extraidos` não vai para o repositório: recrie rodando o script.

## Problemas comuns

- **"Python was not found"**: o Python não está instalado ou não está no PATH. Reinstale marcando "Add python.exe to PATH" e abra um terminal novo.
- **"can't open file"**: o terminal está em outra pasta. Use `cd` para entrar na pasta do script antes de rodar.
- **Erros de `Get-Process` no PowerShell**: foi colado o `PS C:\...>` do começo da linha junto com o comando. Copie só o comando.