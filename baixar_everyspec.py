#!/usr/bin/env python3
"""
Baixa uma amostra de PDFs do EverySpec (https://everyspec.com) para testar um RAG.

Instalação:
    pip install requests beautifulsoup4

Exemplos:
    python baixar_everyspec.py --categorias FED-STD --max-por-categoria 3   # teste rápido
    python baixar_everyspec.py                                              # padrão: 3 categorias x 20
    python baixar_everyspec.py --categorias MIL-HDBK NASA DOE --max-por-categoria 40

Pode interromper (Ctrl+C) e rodar de novo: o que já foi baixado é pulado.
"""

import argparse
import json
import random
import re
import time
from collections import deque
from pathlib import Path
from urllib import robotparser
from urllib.parse import parse_qs, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

BASE = "https://everyspec.com"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
)

# Página de detalhe termina com _<id>/  (ex.: FED-STD-595B_5532/)
RE_DETALHE = re.compile(r"_(\d+)/?$")
# Notices/amendments/changes costumam ter 1-3 páginas: pouco conteúdo para o RAG
RE_ADENDO = re.compile(r"\(\s*(NOTICE|AMENDMENT|CHANGE|CHG|VALIDATION|CANC)", re.I)


class BaixadorEverySpec:
    def __init__(self, pasta, atraso, tamanho_max_mb, incluir_adendos):
        self.pasta = Path(pasta)
        self.pasta.mkdir(parents=True, exist_ok=True)
        self.atraso = atraso
        self.tamanho_max = tamanho_max_mb * 1024 * 1024
        self.incluir_adendos = incluir_adendos

        self.sessao = requests.Session()
        self.sessao.headers.update({"User-Agent": USER_AGENT})
        self.robots = self._carregar_robots()

        self.manifesto = self.pasta / "manifesto.jsonl"
        self.pdfs_feitos, self.paginas_feitas = self._ler_manifesto()

    # ------------------------------------------------------------------ utilidades
    def _pausa(self):
        time.sleep(self.atraso * random.uniform(0.7, 1.5))

    def _carregar_robots(self):
        rp = robotparser.RobotFileParser()
        try:
            r = self.sessao.get(f"{BASE}/robots.txt", timeout=30)
            rp.parse(r.text.splitlines() if r.status_code == 200 else [])
        except requests.RequestException:
            rp.parse([])
        return rp

    def _ler_manifesto(self):
        pdfs, paginas = set(), set()
        if self.manifesto.exists():
            for linha in self.manifesto.read_text(encoding="utf-8").splitlines():
                try:
                    reg = json.loads(linha)
                    pdfs.add(reg["url_pdf"])
                    paginas.add(reg["url_pagina"])
                except (json.JSONDecodeError, KeyError):
                    pass
        return pdfs, paginas

    @staticmethod
    def _normalizar(url):
        p = urlparse(url)
        if "everyspec.com" not in p.netloc.lower():
            return None
        return urlunparse(("https", "everyspec.com", p.path, "", p.query, ""))

    def _get(self, url, tentativas=3, **kwargs):
        for i in range(1, tentativas + 1):
            try:
                r = self.sessao.get(url, timeout=90, **kwargs)
            except requests.RequestException as e:
                print(f"    ! erro de rede ({e.__class__.__name__}), tentativa {i}/{tentativas}")
                time.sleep(5 * i)
                continue
            if r.status_code == 200:
                return r
            if r.status_code in (429, 500, 502, 503, 504):
                espera = 20 * i
                print(f"    ! HTTP {r.status_code}, aguardando {espera}s")
                time.sleep(espera)
                continue
            print(f"    ! HTTP {r.status_code} em {url}")
            return None
        return None

    @staticmethod
    def _nome_arquivo(url_pdf, doc_id):
        spec = parse_qs(urlparse(url_pdf).query).get("spec", [""])[0]
        nome = re.sub(r"[^\w.\-]+", "_", spec).strip("._")
        if not nome.lower().endswith(".pdf"):
            return f"everyspec_{doc_id}.pdf"
        return nome[:-4] + ".pdf"

    # ------------------------------------------------------------------ rastreamento
    def rastrear(self, categoria, limite):
        raiz = f"{BASE}/{categoria.strip('/')}/"
        prefixo = urlparse(raiz).path.lower()
        fila = deque([(raiz, None)])
        vistas = set()
        baixados = paginas = 0
        max_paginas = limite * 15  # trava de segurança

        print(f"\n=== {categoria} (meta: {limite} PDFs novos) ===")
        while fila and baixados < limite and paginas < max_paginas:
            url, titulo = fila.popleft()
            if url in vistas or url in self.paginas_feitas:
                continue
            vistas.add(url)
            if not self.robots.can_fetch(USER_AGENT, url):
                print(f"    - bloqueado pelo robots.txt: {url}")
                continue

            r = self._get(url)
            paginas += 1
            self._pausa()
            if r is None or "html" not in r.headers.get("Content-Type", ""):
                continue
            soup = BeautifulSoup(r.text, "html.parser")

            if RE_DETALHE.search(urlparse(url).path):
                if self._baixar_de_detalhe(soup, url, titulo, categoria):
                    baixados += 1
                continue

            # Página de listagem: junta links (mesmo destino pode aparecer 2x; guarda o texto mais longo)
            candidatos = {}
            for a in soup.find_all("a", href=True):
                link = self._normalizar(urljoin(url, a["href"]))
                if not link or link in vistas:
                    continue
                caminho = urlparse(link).path
                if not caminho.lower().startswith(prefixo) or "download.php" in caminho:
                    continue
                texto = a.get_text(" ", strip=True)
                if len(texto) > len(candidatos.get(link, "")):
                    candidatos[link] = texto
                else:
                    candidatos.setdefault(link, texto)

            for link, texto in candidatos.items():
                if RE_DETALHE.search(urlparse(link).path):
                    if not self.incluir_adendos and RE_ADENDO.search(texto):
                        continue
                    fila.append((link, texto))
                else:
                    fila.append((link, None))  # subcategoria ou paginação

        print(f"--- {categoria}: {baixados} PDFs novos ({paginas} páginas visitadas)")
        return baixados

    def _baixar_de_detalhe(self, soup, url_detalhe, titulo, categoria):
        doc_id = RE_DETALHE.search(urlparse(url_detalhe).path).group(1)

        links = []
        for tag in soup.find_all(["a", "iframe", "embed"]):
            href = tag.get("href") or tag.get("src") or ""
            if "download.php" in href or href.lower().split("?")[0].endswith(".pdf"):
                normal = self._normalizar(urljoin(url_detalhe, href))
                if normal:
                    links.append(normal)
        if not links:
            print(f"    ? nenhum link de PDF em {url_detalhe}")
            return False

        # A página lista várias versões; prefere a que tem o mesmo ID da página
        re_id = re.compile(rf"\.0*{doc_id}\.pdf", re.I)
        url_pdf = next((l for l in links if re_id.search(l)), links[0])
        if url_pdf in self.pdfs_feitos:
            return False

        if not titulo:
            titulo = soup.title.get_text(" ", strip=True) if soup.title else ""
        nome = self._nome_arquivo(url_pdf, doc_id)
        destino = self.pasta / categoria / nome

        print(f"  baixando: {nome}  |  {titulo[:80]}")
        if not self._baixar_arquivo(url_pdf, destino, referer=url_detalhe):
            return False

        registro = {
            "arquivo": str(destino.relative_to(self.pasta)),
            "titulo": titulo,
            "categoria": categoria,
            "doc_id": doc_id,
            "url_pagina": url_detalhe,
            "url_pdf": url_pdf,
        }
        with self.manifesto.open("a", encoding="utf-8") as f:
            f.write(json.dumps(registro, ensure_ascii=False) + "\n")
        self.pdfs_feitos.add(url_pdf)
        self.paginas_feitas.add(url_detalhe)
        return True

    def _baixar_arquivo(self, url, destino, referer, profundidade=0):
        if destino.exists() and destino.stat().st_size > 0:
            return True

        r = self._get(url, stream=True, headers={"Referer": referer})
        self._pausa()
        if r is None:
            return False

        with r:
            tamanho = int(r.headers.get("Content-Length") or 0)
            if tamanho > self.tamanho_max:
                print(f"    - pulado: {tamanho / 1e6:.1f} MB (acima de --tamanho-max-mb)")
                return False

            blocos = r.iter_content(chunk_size=64 * 1024)
            primeiro = next(blocos, b"")

            if b"%PDF" not in primeiro[:1024]:
                # Às vezes o download passa por uma página intermediária em HTML
                if profundidade == 0:
                    html = primeiro + b"".join(blocos)
                    soup = BeautifulSoup(html, "html.parser")
                    for tag in soup.find_all(["a", "iframe", "embed"]):
                        href = tag.get("href") or tag.get("src") or ""
                        if ".pdf" in href.lower():
                            alvo = urljoin(url, href)
                            return self._baixar_arquivo(alvo, destino, referer=url, profundidade=1)
                print(f"    ! resposta não é PDF ({r.headers.get('Content-Type')}): {url}")
                return False

            destino.parent.mkdir(parents=True, exist_ok=True)
            temporario = destino.with_name(destino.name + ".part")
            total = len(primeiro)
            with temporario.open("wb") as f:
                f.write(primeiro)
                for bloco in blocos:
                    total += len(bloco)
                    if total > self.tamanho_max:
                        f.close()
                        temporario.unlink(missing_ok=True)
                        print("    - pulado: arquivo passou do limite de tamanho")
                        return False
                    f.write(bloco)
            temporario.replace(destino)
            print(f"    ok ({total / 1e6:.2f} MB)")
        return True


def main():
    ap = argparse.ArgumentParser(description="Baixa uma amostra de PDFs do EverySpec para testes de RAG.")
    ap.add_argument("--categorias", nargs="+", default=["FED-STD", "MIL-HDBK", "NASA"],
                    help="nome como aparece na URL: FED-STD, MIL-HDBK, MIL-STD, NASA, FAA, DOE, "
                         "FED_SPECS, DATA-ITEM-DESC-DIDs, MS-Specs...")
    ap.add_argument("--max-por-categoria", type=int, default=20)
    ap.add_argument("--pasta", default="pdfs_everyspec")
    ap.add_argument("--atraso", type=float, default=2.0, help="segundos (aprox.) entre requisições")
    ap.add_argument("--tamanho-max-mb", type=float, default=30)
    ap.add_argument("--incluir-adendos", action="store_true",
                    help="baixa também NOTICE/AMENDMENT/CHANGE (geralmente só 1-3 páginas)")
    args = ap.parse_args()

    baixador = BaixadorEverySpec(args.pasta, args.atraso, args.tamanho_max_mb, args.incluir_adendos)
    total = 0
    try:
        for categoria in args.categorias:
            total += baixador.rastrear(categoria, args.max_por_categoria)
    except KeyboardInterrupt:
        print("\nInterrompido. Rode de novo para continuar de onde parou.")
    print(f"\nTotal de PDFs novos: {total}")
    print(f"Pasta: {Path(args.pasta).resolve()}")
    print(f"Metadados: {baixador.manifesto.resolve()}")


if __name__ == "__main__":
    main()