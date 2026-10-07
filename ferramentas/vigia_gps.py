#!/usr/bin/env python3
"""Vigia do GPS da Prefeitura (roda no GitHub a cada 5 minutos).

Testa todos os endereços de GPS dos ônibus listados em fontes_gps.json,
conta quantos ônibus diferentes cada um está mandando e grava em
"melhor" o endereço com MAIS ônibus. Os celulares só leem esse arquivo
(1 KB), sem gastar internet testando.

Só grava (e o GitHub só publica) quando a escolha MUDA. Se todos vierem
zerados (de madrugada a Prefeitura para), não mexe em nada.
"""
import datetime as dt
import gzip
import json
import os
import sys
import urllib.request

ARQUIVO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "fontes_gps.json")
FOLGA = 1.10  # a outra precisa ter 10% a mais de ônibus para trocar


def data(d, formato):
    dia, hora = d.strftime("%Y-%m-%d"), d.strftime("%H:%M:%S")
    return f"{dia}+{hora}" if formato == "espaco" else f"{dia}T{hora}Z"


def contar(fonte, desde, ate):
    url = f"{fonte['url']}?dataInicial={data(desde, fonte.get('formato'))}&dataFinal={data(ate, fonte.get('formato'))}"
    req = urllib.request.Request(url, headers={"Accept-Encoding": "gzip", "User-Agent": "GuiaDoMotorista-vigia/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            corpo = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                corpo = gzip.decompress(corpo)
        d = json.loads(corpo)
    except Exception as e:  # fora do ar, erro, resposta estranha
        print(f"  {fonte['nome']}: erro ({e})")
        return 0
    lista = d if isinstance(d, list) else (d.get("data") if isinstance(d, dict) else None) or []
    carros = {str(x.get("id_veiculo", "")).strip() for x in lista if isinstance(x, dict)}
    carros.discard("")
    return len(carros)


def main():
    with open(ARQUIVO, encoding="utf-8") as f:
        cfg = json.load(f)
    fontes = [x for x in cfg.get("fontes", []) if str(x.get("url", "")).startswith("https://")]
    if not fontes:
        print("Nenhum endereço na lista.")
        return
    agora = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    desde, ate = agora - dt.timedelta(seconds=40), agora + dt.timedelta(minutes=1)

    contagem = {}
    for x in fontes:
        contagem[x["url"]] = contar(x, desde, ate)
        print(f"  {x['nome']}: {contagem[x['url']]} ônibus")

    atual = cfg.get("melhor") or fontes[0]["url"]
    escolhida, maximo = atual, contagem.get(atual, 0)
    for x in fontes:
        n = contagem[x["url"]]
        if x["url"] != atual and n > 0 and n > maximo * FOLGA:
            escolhida, maximo = x["url"], n

    mudou = escolhida != cfg.get("melhor")
    if mudou:
        cfg["melhor"] = escolhida
        cfg["escolhidoEm"] = agora.isoformat().replace("+00:00", "Z")
        cfg["contagem"] = {x["nome"]: contagem[x["url"]] for x in fontes}
        with open(ARQUIVO, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
            f.write("\n")
        print(f"TROQUEI para: {escolhida}")
    else:
        print(f"Continua: {escolhida}")

    saida = os.environ.get("GITHUB_OUTPUT")
    if saida:
        with open(saida, "a", encoding="utf-8") as f:
            f.write(f"mudou={'true' if mudou else 'false'}\n")
            f.write(f"escolhida={escolhida}\n")


if __name__ == "__main__":
    sys.exit(main())
