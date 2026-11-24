"""
Coleta documentos públicos para treinar o classificador de tipo de documento
(Técnica, Jurídica, Normativa, Quantitativa) e monta a planilha que a equipe
rotula.

Rodar da raiz do repositório, com o ambiente do back (.venv):
    python -m classificador.coletar_amostras                     # 30 por fonte
    python -m classificador.coletar_amostras --por-fonte 5       # teste rápido
    python -m classificador.coletar_amostras --fontes manual   # só os PDFs próprios

Saída (pasta amostras_tipo/, fora do git):
    amostras_tipo/<fonte>/*.pdf     os PDFs baixados
    amostras_tipo/amostras.csv      uma linha por documento: fonte, tipo_sugerido,
                                    tipo (vazio: a equipe preenche), título, URL,
                                    arquivo e o texto que a rede lê

O `tipo_sugerido` é só o palpite pela fonte (um handbook do EverySpec costuma
ser Técnica). Quem decide é a equipe, na coluna `tipo`: abrir o CSV no Google
Planilhas, conferir o começo do texto (ou o PDF) e escrever um dos 4 tipos.
Linha com `tipo` vazio não entra no treino; para descartar um documento,
escreva "descartar". Os critérios de cada tipo estão em classificador/README.md.

Documentos da própria empresa (em português, por exemplo): coloque os PDFs em
amostras_tipo/manual/<Tipo>/ (ex.: amostras_tipo/manual/Jurídica/contrato.pdf)
e rode com --fontes manual. O nome da pasta vira o tipo_sugerido.

Pode interromper (Ctrl+C) e rodar de novo: o que já está no CSV é pulado.
O texto sai de core_api.services.classificador_tipo.texto_do_pdf, a mesma
função que o back usa no upload: treino e uso leem o documento igual.
"""

import argparse
import csv
import json
import os
import random
import re
import sys
import time
from pathlib import Path

import requests

from core_api.management.commands.baixar_everyspec import BaixadorEverySpec
from core_api.services.classificador_tipo import CARACTERES_MINIMOS, texto_do_pdf

TIPOS = ("Técnica", "Jurídica", "Normativa", "Quantitativa")
COLUNAS = [
    "id",
    "fonte",
    "tipo_sugerido",
    "tipo",
    "titulo",
    "url",
    "arquivo",
    "texto",
]
USER_AGENT = "coletor-de-amostras/1.0 (uso academico)"
TAMANHO_MAXIMO = 30 * 1024 * 1024

# fonte -> (tipo sugerido, descrição). A ordem é a do --fontes padrão.
FONTES = {
    "everyspec-mil-hdbk": ("Técnica", "EverySpec, MIL-HDBK (handbooks militares)"),
    "ntrs-tecnica": ("Técnica", "NASA NTRS, guias de usuário e de projeto"),
    "everyspec-mil-std": ("Normativa", "EverySpec, MIL-STD (normas militares)"),
    "everyspec-fed-std": ("Normativa", "EverySpec, FED-STD (normas federais)"),
    "govinfo-tribunais": ("Jurídica", "GovInfo, decisões de tribunais dos EUA"),
    "federal-register": ("Jurídica", "Federal Register, regras sobre aeronaves"),
    "ntrs-quantitativa": ("Quantitativa", "NASA NTRS, relatórios de dados de ensaio"),
    "manual": (None, "PDFs colocados em amostras_tipo/manual/<Tipo>/"),
}

EVERYSPEC = {
    "everyspec-mil-hdbk": "MIL-HDBK",
    "everyspec-mil-std": "MIL-STD",
    "everyspec-fed-std": "FED-STD",
}
NTRS_BUSCAS = {
    "ntrs-tecnica": ["user guide", "design guide", "handbook procedures"],
    "ntrs-quantitativa": [
        "wind tunnel test data",
        "flight test data report",
        "statistical data tables",
    ],
}
# Resumos e apresentações quase não têm texto: ficam de fora.
NTRS_TIPOS_ACEITOS = {
    "TECHNICAL_MEMORANDUM",
    "TECHNICAL_REPORT",
    "CONTRACTOR_REPORT",
    "TECHNICAL_PUBLICATION",
    "REFERENCE_PUBLICATION",
    "OTHER",
}


class Coletor:
    def __init__(self, pasta, por_fonte, atraso, chave_govinfo):
        self.pasta = Path(pasta)
        self.pasta.mkdir(parents=True, exist_ok=True)
        self.por_fonte = por_fonte
        self.atraso = atraso
        self.chave_govinfo = chave_govinfo
        self.sessao = requests.Session()
        self.sessao.headers["User-Agent"] = USER_AGENT
        self.csv = self.pasta / "amostras.csv"
        self.urls_feitas = set()
        self.proximo_id = 1
        if self.csv.exists():
            with self.csv.open(encoding="utf-8-sig", newline="") as f:
                for linha in csv.DictReader(f):
                    self.urls_feitas.add(linha["url"])
                    self.proximo_id = max(self.proximo_id, int(linha["id"]) + 1)
        else:
            with self.csv.open("w", encoding="utf-8-sig", newline="") as f:
                csv.DictWriter(f, COLUNAS).writeheader()

    # -- utilidades -------------------------------------------------------
    def _pausa(self):
        time.sleep(self.atraso * random.uniform(0.7, 1.5))

    @staticmethod
    def _nome(texto):
        nome = re.sub(r"[^\w.\-]+", "_", texto).strip("._")[:80]
        return (nome or "documento") + ".pdf"

    def _baixar(self, url, destino):
        """Baixa o PDF (com limite de tamanho). True se o arquivo ficou ok."""
        if destino.exists() and destino.stat().st_size > 0:
            return True
        try:
            with self.sessao.get(url, stream=True, timeout=120) as r:
                if r.status_code != 200:
                    print(f"    ! HTTP {r.status_code}: {url}")
                    return False
                blocos = r.iter_content(64 * 1024)
                primeiro = next(blocos, b"")
                if b"%PDF" not in primeiro[:1024]:
                    print(f"    ! não é PDF: {url}")
                    return False
                destino.parent.mkdir(parents=True, exist_ok=True)
                temporario = destino.with_name(destino.name + ".part")
                total = len(primeiro)
                with temporario.open("wb") as f:
                    f.write(primeiro)
                    for bloco in blocos:
                        total += len(bloco)
                        if total > TAMANHO_MAXIMO:
                            break
                        f.write(bloco)
                if total > TAMANHO_MAXIMO:
                    temporario.unlink(missing_ok=True)
                    print(f"    - pulado (mais de 30 MB): {url}")
                    return False
                temporario.replace(destino)
                return True
        except requests.RequestException as e:
            print(f"    ! erro de rede ({e.__class__.__name__}): {url}")
            return False
        finally:
            self._pausa()

    def _registrar(self, fonte, tipo_sugerido, titulo, url, arquivo):
        """Extrai o texto e grava a linha no CSV. True se entrou."""
        try:
            texto = texto_do_pdf(arquivo)
        except Exception as e:  # noqa: BLE001 - PDF corrompido, protegido etc.
            print(f"    ! PDF ilegível ({e.__class__.__name__}): {arquivo.name}")
            return False
        if len(texto) < CARACTERES_MINIMOS:
            print(f"    - sem texto (digitalizado?): {arquivo.name}")
            return False
        linha = {
            "id": self.proximo_id,
            "fonte": fonte,
            "tipo_sugerido": tipo_sugerido,
            "tipo": "",
            "titulo": titulo.strip()[:300],
            "url": url,
            "arquivo": arquivo.relative_to(self.pasta).as_posix(),
            "texto": texto,
        }
        with self.csv.open("a", encoding="utf-8-sig", newline="") as f:
            csv.DictWriter(f, COLUNAS).writerow(linha)
        self.proximo_id += 1
        self.urls_feitas.add(url)
        print(f"  + [{tipo_sugerido}] {titulo[:70]}")
        return True

    def _baixar_e_registrar(self, fonte, candidatos):
        """candidatos: iterável de (titulo, url_pdf). Para no --por-fonte."""
        tipo, _ = FONTES[fonte]
        feitos = 0
        for titulo, url in candidatos:
            if feitos >= self.por_fonte:
                break
            if url in self.urls_feitas:
                continue
            destino = self.pasta / fonte / self._nome(titulo or url.rsplit("/", 1)[-1])
            if self._baixar(url, destino) and self._registrar(
                fonte, tipo, titulo, url, destino
            ):
                feitos += 1
        return feitos

    # -- fontes -----------------------------------------------------------
    def everyspec(self, fonte):
        """Usa o rastreador do EverySpec que o RAG já usa (baixar_everyspec)."""
        categoria = EVERYSPEC[fonte]
        pasta = self.pasta / fonte
        baixador = BaixadorEverySpec(
            pasta, self.atraso, tamanho_max_mb=30, incluir_adendos=False
        )
        baixador.rastrear(categoria, self.por_fonte)
        tipo, _ = FONTES[fonte]
        feitos = 0
        for linha in baixador.manifesto.read_text(encoding="utf-8").splitlines():
            reg = json.loads(linha)
            if reg["url_pdf"] in self.urls_feitas:
                continue
            arquivo = pasta / reg["arquivo"]
            if arquivo.is_file() and self._registrar(
                fonte, tipo, reg["titulo"], reg["url_pdf"], arquivo
            ):
                feitos += 1
        return feitos

    def ntrs(self, fonte):
        def candidatos():
            por_busca = max(self.por_fonte, 10)
            for busca in NTRS_BUSCAS[fonte]:
                r = self.sessao.post(
                    "https://ntrs.nasa.gov/api/citations/search",
                    json={"q": busca, "page": {"size": por_busca, "from": 0}},
                    timeout=60,
                )
                self._pausa()
                if r.status_code != 200:
                    print(f"    ! NTRS HTTP {r.status_code} na busca '{busca}'")
                    continue
                for item in r.json().get("results", []):
                    if item.get("stiType") not in NTRS_TIPOS_ACEITOS:
                        continue
                    for download in item.get("downloads", []):
                        pdf = download.get("links", {}).get("pdf")
                        if pdf:
                            yield item["title"], "https://ntrs.nasa.gov" + pdf
                            break

        return self._baixar_e_registrar(fonte, candidatos())

    def govinfo_tribunais(self, fonte):
        def candidatos():
            r = self.sessao.post(
                "https://api.govinfo.gov/search",
                params={"api_key": self.chave_govinfo},
                json={
                    "query": "collection:USCOURTS (aircraft OR aviation OR FAA)",
                    "pageSize": min(self.por_fonte * 3, 100),
                    "offsetMark": "*",
                    "sorts": [{"field": "score", "sortOrder": "DESC"}],
                },
                timeout=60,
            )
            self._pausa()
            if r.status_code != 200:
                print(f"    ! GovInfo HTTP {r.status_code}: {r.text[:200]}")
                return
            for item in r.json().get("results", []):
                pacote, granulo = item.get("packageId"), item.get("granuleId")
                if pacote and granulo:
                    yield (
                        item.get("title", granulo),
                        f"https://www.govinfo.gov/content/pkg/{pacote}/pdf/{granulo}.pdf",
                    )

        return self._baixar_e_registrar(fonte, candidatos())

    def federal_register(self, fonte):
        def candidatos():
            r = self.sessao.get(
                "https://www.federalregister.gov/api/v1/documents.json",
                params={
                    "conditions[term]": "aircraft",
                    "conditions[type][]": "RULE",
                    "per_page": min(self.por_fonte * 3, 100),
                    "fields[]": ["title", "pdf_url"],
                },
                timeout=60,
            )
            self._pausa()
            if r.status_code != 200:
                print(f"    ! Federal Register HTTP {r.status_code}")
                return
            for item in r.json().get("results", []):
                if item.get("pdf_url"):
                    yield item["title"], item["pdf_url"]

        return self._baixar_e_registrar(fonte, candidatos())

    def manual(self, fonte):
        feitos = 0
        for tipo in TIPOS:
            for arquivo in sorted((self.pasta / "manual" / tipo).glob("*.pdf")):
                url = f"manual/{tipo}/{arquivo.name}"
                if url not in self.urls_feitas and self._registrar(
                    fonte, tipo, arquivo.stem, url, arquivo
                ):
                    feitos += 1
        return feitos

    def coletar(self, fonte):
        _, descricao = FONTES[fonte]
        print(f"\n=== {fonte}: {descricao} (meta: {self.por_fonte}) ===")
        if fonte in EVERYSPEC:
            feitos = self.everyspec(fonte)
        elif fonte in NTRS_BUSCAS:
            feitos = self.ntrs(fonte)
        else:
            feitos = getattr(self, fonte.replace("-", "_"))(fonte)
        print(f"--- {fonte}: {feitos} documento(s) novo(s) no CSV")
        return feitos


def resumo(caminho_csv):
    contagem = {}
    with open(caminho_csv, encoding="utf-8-sig", newline="") as f:
        for linha in csv.DictReader(f):
            chave = linha["tipo"] or f"(sem rótulo, sugerido {linha['tipo_sugerido']})"
            contagem[chave] = contagem.get(chave, 0) + 1
    for chave, total in sorted(contagem.items()):
        print(f"  {total:4d}  {chave}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument(
        "--fontes",
        nargs="+",
        choices=list(FONTES),
        default=[f for f in FONTES if f != "manual"],
    )
    ap.add_argument("--por-fonte", type=int, default=30)
    ap.add_argument("--pasta", default="amostras_tipo")
    ap.add_argument(
        "--atraso", type=float, default=2.0, help="segundos entre requisições"
    )
    ap.add_argument(
        "--chave-govinfo",
        default=os.getenv("GOVINFO_API_KEY", "DEMO_KEY"),
        help="chave grátis em https://api.data.gov/signup (a DEMO_KEY tem limite "
        "baixo por hora)",
    )
    args = ap.parse_args()

    coletor = Coletor(args.pasta, args.por_fonte, args.atraso, args.chave_govinfo)
    total = 0
    try:
        for fonte in args.fontes:
            total += coletor.coletar(fonte)
    except KeyboardInterrupt:
        print("\nInterrompido. Rode de novo para continuar de onde parou.")
    print(f"\nDocumentos novos: {total}. Planilha: {coletor.csv.resolve()}")
    resumo(coletor.csv)
    return 0


if __name__ == "__main__":
    sys.exit(main())
