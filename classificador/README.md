# Classificador do tipo de documento

No upload, assim que o usuário escolhe o PDF, o formulário já vem com o tipo
sugerido: **Técnica**, **Jurídica**, **Normativa** ou **Quantitativa**. O
usuário confirma ou troca. A sugestão vem de uma rede neural simples,
treinada com documentos públicos rotulados pela equipe.

```
PDF ──► texto (4000 primeiros caracteres) ──► bge-m3 (Ollama) ──► vetor de 1024
    ──► rede neural 1024 → 64 (ReLU) → 4 (softmax) ──► probabilidade de cada tipo
```

| Arquivo | O que faz |
| --- | --- |
| `classificador/coletar_amostras.py` | baixa PDFs públicos e monta `amostras_tipo/amostras.csv` para a equipe rotular |
| `classificador/treino_classificador.ipynb` | notebook do Colab: embeddings, treino, métricas e exportação do `.npz` |
| `classificador/modelo_tipo_documento.npz` | os pesos treinados (entra no repositório depois do primeiro treino) |
| `core_api/services/classificador_tipo.py` | no back: lê o PDF, pede o vetor ao Ollama e faz a conta da rede com numpy |
| `POST /api/documents/sugerir-tipo/` | rota que o formulário chama ao escolher o arquivo |

Sem o `.npz`, com o Ollama fora do ar ou com PDF sem texto, a rota devolve
`{"sugestao": null, "motivo": "..."}` e o formulário segue como antes.

## Os 4 tipos (critério para rotular)

Olhe o título e o começo do texto e pergunte **para que o documento serve**:

| Tipo | Serve para | Exemplos | Pistas no texto |
| --- | --- | --- | --- |
| **Técnica** | explicar como algo funciona ou como fazer | manual, handbook, guia de usuário, procedimento, relatório descritivo | "this handbook", "procedure", passo a passo, figuras explicativas |
| **Jurídica** | criar, aplicar ou interpretar uma obrigação legal | lei, decreto, regra publicada (Federal Register), contrato, decisão judicial, parecer | "court", "plaintiff", "pursuant to", "effective date", artigos e incisos |
| **Normativa** | definir requisitos que algo precisa cumprir | MIL-STD, FED-STD, especificação, norma ABNT/ISO, RBAC | "shall", "requirements", "conformance", "this standard" |
| **Quantitativa** | apresentar dados medidos ou calculados | relatório de ensaio com tabelas, catálogo de dados, estatística, anuário | tabelas e números na maior parte do texto, "data", "results" |

Na dúvida entre dois tipos, vale o propósito principal. Uma norma cheia de
tabelas continua Normativa. Um relatório que mais explica do que mede é Técnica.
Documento ilegível ou fora do assunto: escreva `descartar`.

## Passo a passo

1. **Coletar** (na raiz do back, com o `.venv`):
   ```
   python -m classificador.coletar_amostras --por-fonte 30
   ```
   São 7 fontes públicas: EverySpec (MIL-HDBK, MIL-STD, FED-STD), NASA NTRS
   (guias e relatórios de ensaio), GovInfo (decisões de tribunais dos EUA) e
   Federal Register (regras sobre aeronaves). Dá cerca de 200 documentos.
   O GovInfo usa a `DEMO_KEY`, que tem limite por hora. Uma chave grátis sai
   em https://api.data.gov/signup e vai em `GOVINFO_API_KEY`.
2. **Documentos próprios** (recomendado, principalmente em português): coloque
   os PDFs em `amostras_tipo/manual/<Tipo>/` e rode com `--fontes manual`.
3. **Rotular**: importe `amostras_tipo/amostras.csv` no Google Planilhas,
   divida as linhas entre a equipe e preencha a coluna `tipo`. A coluna
   `tipo_sugerido` é só o palpite pela fonte. Baixe de volta como CSV.
4. **Treinar**: abra `treino_classificador.ipynb` no Colab (com GPU), envie o
   CSV e rode tudo. No fim sai o `modelo_tipo_documento.npz`.
5. **Publicar**: copie o `.npz` para esta pasta e abra um PR. O deploy leva o
   arquivo para a VM do back.

## Resultado do modelo atual

183 documentos públicos (58 Técnica, 47 Normativa, 43 Jurídica e 35
Quantitativa), rotulados pelo palpite da fonte e com ajuda de IA. Os 13 casos
duvidosos foram conferidos pela equipe; os demais não foram lidos um a um.
Documentos repetidos (a mesma norma em várias revisões, a mesma diretriz com
outra data) saíram antes do treino: cópia no treino e no teste infla a
acurácia.

Acurácia de **81,1%** em 37 documentos que a rede não viu no treino:

| Tipo | Precisão | Recall | Documentos no teste |
| --- | --- | --- | --- |
| Técnica | 0,73 | 0,67 | 12 |
| Jurídica | 1,00 | 1,00 | 9 |
| Normativa | 1,00 | 1,00 | 9 |
| Quantitativa | 0,50 | 0,57 | 7 |

Os 7 erros são entre Técnica e Quantitativa; em 4 deles o PDF começa com a
página padrão dos relatórios da NASA, igual nos dois tipos. São poucos
documentos de teste, todos em inglês e das mesmas fontes do treino: em
documentos de outras origens o acerto pode ser menor.

## Cuidados

- **A rede pode aprender a fonte em vez do tipo.** Todo documento do Federal
  Register começa com "Federal Register / Vol.". Por isso quase
  todo tipo tem mais de uma fonte (Quantitativa só tem a NASA NTRS), e os
  documentos próprios da pasta `manual/` ajudam muito.
  Confira a seção "Onde a rede errou" do notebook.
- **Treino e uso leem o mesmo texto**: o coletor usa
  `classificador_tipo.texto_do_pdf`, a mesma função do upload. Mudou
  `CARACTERES_ENTRADA`? Rode o coletor de novo e retreine.
- **Mesmo embedding**: o notebook usa o `BAAI/bge-m3` (sentence-transformers),
  e o back usa o `bge-m3` do Ollama. Os dois carregam os mesmos pesos.
- **Os 9 tipos antigos** continuam no banco com `ativo=False` (migration 0016)
  e comentados em `seed_taxonomia.py`. Documentos antigos mantêm o tipo que tinham.
