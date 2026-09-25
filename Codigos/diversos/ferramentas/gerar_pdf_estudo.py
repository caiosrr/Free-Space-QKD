"""Gera PDFs dos documentos de estudo, para ler e anotar no tablet pelo Zotero.

O app do Zotero no tablet abre PDF, mas nao Markdown. Este programa converte os
documentos numerados de Anotacoes/ (01_, 02_, ...) com pandoc e XeLaTeX, e grava
em Arquivos/estudo/, que fica fora do git.

Cada PDF e uma FOTOGRAFIA do documento: o topo diz contra qual commit ele foi
conferido. Quando um documento muda, gere de novo e substitua o anexo no
Zotero. As anotacoes feitas no PDF antigo ficam no PDF antigo.

Uso, a partir da pasta Codigos:

    python diversos/ferramentas/gerar_pdf_estudo.py
    python diversos/ferramentas/gerar_pdf_estudo.py 06

Precisa de pandoc e de uma distribuicao LaTeX com xelatex (MiKTeX serve).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[3]
ANOTACOES = RAIZ / "Anotaçoes"
SAIDA = RAIZ / "Arquivos" / "estudo"

# Fontes do Windows, com os simbolos que os documentos usam (graus, segundos de
# arco, letras gregas). A fonte padrao do LaTeX nao tem varios deles.
OPCOES = [
    "--pdf-engine=xelatex",
    "-V", "mainfont=Cambria",
    "-V", "monofont=Consolas",
    "-V", "mathfont=Cambria Math",
    "-V", "geometry:margin=2cm",
    "-V", "fontsize=11pt",
    "--syntax-highlighting=tango",
]


def main() -> int:
    if shutil.which("pandoc") is None:
        print("pandoc nao encontrado. Instale com: winget install JohnMacFarlane.Pandoc")
        return 1
    filtro = sys.argv[1] if len(sys.argv) > 1 else ""
    documentos = sorted(p for p in ANOTACOES.glob("[0-9][0-9]_*.md")
                        if p.name.startswith(filtro))
    if not documentos:
        print(f"Nenhum documento de estudo em {ANOTACOES} comecando com '{filtro}'.")
        return 1

    SAIDA.mkdir(parents=True, exist_ok=True)
    falhas = 0
    for md in documentos:
        pdf = SAIDA / md.with_suffix(".pdf").name
        print(f"  {md.name} -> {pdf.name} ... ", end="", flush=True)
        r = subprocess.run(["pandoc", str(md), "-o", str(pdf), *OPCOES],
                           capture_output=True, text=True, cwd=ANOTACOES)
        if r.returncode == 0 and pdf.exists():
            print("ok")
        else:
            falhas += 1
            print("FALHOU")
            print("    " + (r.stderr.strip().splitlines() or ["sem mensagem"])[-1])
    print(f"\nPDFs em {SAIDA}")
    return 1 if falhas else 0


if __name__ == "__main__":
    raise SystemExit(main())
