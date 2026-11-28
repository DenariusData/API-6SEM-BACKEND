"""
Gera monitoring/stress/relatorio.html (página com os gráficos) a partir de
relatorio_modelo.html e de resultados/resumo.json (rode antes o analisar.py).

    python monitoring/stress/gerar_relatorio.py
"""

import json
from pathlib import Path

AQUI = Path(__file__).resolve().parent

resumo = json.loads((AQUI / "resultados" / "resumo.json").read_text(encoding="utf-8"))
for vm in ("front", "back", "ia", "monitor"):
    if vm in resumo:
        resumo[vm].pop("eventos", None)
modelo = (AQUI / "relatorio_modelo.html").read_text(encoding="utf-8")
html = modelo.replace("/*DADOS*/null", json.dumps(resumo, ensure_ascii=False, separators=(",", ":")))
(AQUI / "relatorio.html").write_text(html, encoding="utf-8")
print("->", AQUI / "relatorio.html", f"({len(html) // 1024} KB)")
