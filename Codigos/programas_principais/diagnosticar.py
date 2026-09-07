"""Diagnostico de camera e sinal antes da sessao. NAO move o mount.

Responde, sem gastar uma sessao inteira: quanto sinal existe, quanta escala do
sensor esta em uso, qual a escala fisica do enlace e se o centroide fecha.

    python programas_principais/diagnosticar.py --camera ids
    python programas_principais/diagnosticar.py --camera ids --exposicao 1200 10000
    python programas_principais/diagnosticar.py --camera ids --calibrar-pixels
"""

import sys
from pathlib import Path

CODIGOS_DIR = Path(__file__).resolve().parent.parent
if str(CODIGOS_DIR) not in sys.path:
    sys.path.insert(0, str(CODIGOS_DIR))

import argparse

from programas_principais._iniciador import aplicar_camera, perguntar_camera


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--camera", choices=["asi", "ids", "zwo"], default=None)
    parser.add_argument(
        "--exposicao",
        type=float,
        nargs="*",
        default=None,
        metavar="US",
        help="Uma ou mais exposicoes em us para comparar. Sem isso, usa a do perfil.",
    )
    parser.add_argument("--frames", type=int, default=20)
    parser.add_argument(
        "--calibrar-pixels",
        action="store_true",
        help="Mede a mascara de pixels defeituosos com o feixe bloqueado.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    camera = aplicar_camera(args.camera or perguntar_camera({"1": "asi", "2": "ids"}, "2"))

    from modulos.configuracoes import optica
    from modulos.controle.cameras.backend import connect_camera, disconnect_camera
    from modulos.visao import detector_ilhas as foco
    from modulos.visao import diagnostico

    print(f"\n=== DIAGNOSTICO | camera {camera} ===\n")
    print(f"Escala optica: {optica.resumo()}")
    for aviso in optica.avisos():
        print(f"  ATENCAO: {aviso}")

    connect_camera()
    try:
        foco.set_focus_mode("dual")
        if args.calibrar_pixels:
            diagnostico.calibrar_pixels_ruins(foco.EXPOSURE_SECONDS, args.frames)
            print("\nDestampe a objetiva e libere o feixe para o restante do diagnostico.")
            input("Pressione ENTER para continuar...")

        if diagnostico.carregar_mascara_se_existir():
            print("Mascara de pixels ruins: ATIVA")
        else:
            print("Mascara de pixels ruins: ausente (rode com --calibrar-pixels)")

        exposicoes = args.exposicao or [foco.EXPOSURE_SECONDS * 1e6]
        print(f"\nMedindo {len(exposicoes)} exposicao(oes), {args.frames} frames cada:\n")
        resultados = []
        for us in exposicoes:
            resultado = diagnostico.medir(float(us) * 1e-6, args.frames)
            diagnostico.imprimir(resultado)
            resultados.append(resultado)
            print()

        if len(resultados) > 1:
            print("=== COMPARACAO ===")
            base = resultados[0]
            for resultado in resultados[1:]:
                razao_snr = resultado["snr"] / max(base["snr"], 1e-9)
                razao_taxa = resultado["taxa_hz"] / max(base["taxa_hz"], 1e-9)
                print(
                    f"  {resultado['exposicao_us']:.0f} us vs {base['exposicao_us']:.0f} us: "
                    f"SNR x{razao_snr:.2f} | taxa x{razao_taxa:.2f} | "
                    f"dispersao {base.get('dispersao_px', float('nan')):.3f} -> "
                    f"{resultado.get('dispersao_px', float('nan')):.3f} px"
                )
            print(
                "\n  Se a dispersao cair sem a taxa cair junto, a exposicao maior "
                "compensa: o laco nao e limitado pela exposicao."
            )
    finally:
        try:
            disconnect_camera()
        except Exception:
            pass
    print("\n=== FIM DO DIAGNOSTICO ===\n")
