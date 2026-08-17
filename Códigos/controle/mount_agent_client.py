import argparse
import json
from urllib import request


DEFAULT_AGENT_URL = "http://10.4.0.145:18080"


def call_json(method: str, url: str, payload: dict | None = None) -> dict:
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = request.Request(url, data=data, headers=headers, method=method)
    with request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def print_result(result: dict) -> None:
    print(json.dumps(result, indent=2))


def ask_float(prompt: str, default: float) -> float:
    text = input(f"{prompt} [{default}]: ").strip().replace(",", ".")
    if not text:
        return default
    return float(text)


def interactive(agent_url: str) -> None:
    typed_url = input(f"URL do agente remoto [{agent_url}]: ").strip()
    base = (typed_url or agent_url).rstrip("/")

    print("\nCliente interativo do mount remoto")
    print(f"Agente: {base}")

    while True:
        print("\n1 - Testar conexao")
        print("2 - Ler posicao")
        print("3 - Mover relativo")
        print("4 - Parar movimento")
        print("0 - Sair")
        choice = input("Opcao: ").strip()

        try:
            if choice == "1":
                print_result(call_json("GET", f"{base}/health"))
            elif choice == "2":
                print_result(call_json("GET", f"{base}/position"))
            elif choice == "3":
                delta_az = ask_float("Delta Azimute em graus", 0.0)
                delta_alt = ask_float("Delta Altitude em graus", 0.0)
                tolerance = ask_float("Tolerancia em graus", 0.0005)
                if delta_az == 0.0 and delta_alt == 0.0:
                    print("Nenhum movimento pedido.")
                    continue
                print_result(
                    call_json(
                        "POST",
                        f"{base}/move_relative",
                        {
                            "delta_az_deg": delta_az,
                            "delta_alt_deg": delta_alt,
                            "tolerance_deg": tolerance,
                        },
                    )
                )
            elif choice == "4":
                print_result(call_json("POST", f"{base}/stop", {}))
            elif choice == "0":
                break
            else:
                print("Opcao invalida.")
        except Exception as exc:
            print(f"Erro: {exc}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Small client for controle/mount_agent.py.")
    parser.add_argument("--agent-url", default=DEFAULT_AGENT_URL)
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("health")
    sub.add_parser("position")
    sub.add_parser("stop")

    move = sub.add_parser("move")
    move.add_argument("--az", type=float, default=0.0, help="Relative azimuth move in degrees.")
    move.add_argument("--alt", type=float, default=0.0, help="Relative altitude move in degrees.")
    move.add_argument("--tol", type=float, default=0.005, help="Move tolerance in degrees.")

    args = parser.parse_args()
    base = args.agent_url.rstrip("/")

    if args.command == "health":
        result = call_json("GET", f"{base}/health")
    elif args.command == "position":
        result = call_json("GET", f"{base}/position")
    elif args.command == "stop":
        result = call_json("POST", f"{base}/stop", {})
    elif args.command == "move":
        result = call_json(
            "POST",
            f"{base}/move_relative",
            {
                "delta_az_deg": args.az,
                "delta_alt_deg": args.alt,
                "tolerance_deg": args.tol,
            },
        )
    elif args.command is None:
        interactive(args.agent_url)
        return
    else:
        raise RuntimeError(f"Comando desconhecido: {args.command}")

    print_result(result)


if __name__ == "__main__":
    main()
