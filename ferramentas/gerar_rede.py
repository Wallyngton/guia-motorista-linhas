#!/usr/bin/env python3
"""
Gera a "rede" da cidade: um arquivo pequeno com TODAS as linhas e os pontos
de cada uma, na ordem, para o app calcular "Vamos para onde?" no celular
(qual ônibus pegar, onde descer, baldeação), sem servidor.

    python ferramentas/gerar_rede.py --cidade rio

Lê catalogo.json e os arquivos <cidade>/<linha>-<sentido>.json e grava
<cidade>/rede.json. Só usa a biblioteca padrão do Python.

Formato (versão 1):
{
  "formato": 1, "cidade": "rio", "gerado": "2026-10-07 17:00",
  "pontos": [[lat, lng, "nome"], ...],          # cada ponto de ônibus uma vez só
  "sentidos": [
    {"id": "862-0", "l": "862", "n": "Rio das Pedras - Barra", "d": "Circular",
     "c": 1,                                      # 1 = circular (só um sentido)
     "p": [3, 17, ...],                           # índices em "pontos", na ordem
     "m": [0, 412, ...]}                          # metros desde o início, de cada ponto
  ]
}
"""
import argparse
import json
import math
import os

PASTA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


def distancia(a, b):
    """Metros entre (lat, lng) a e b (haversine, igual ao app)."""
    r = 6371000.0
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    d = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * r * math.asin(math.sqrt(d))


def projetar(p, trajeto, acum, desde, perto=25.0):
    """(metros ao longo do trajeto, afastamento, índice do trecho) do ponto p.

    Pega o PRIMEIRO lugar do trajeto (a partir de `desde`) que passa a menos
    de `perto` metros do ponto — numa linha circular o começo e o fim ficam
    no mesmo lugar, e o ponto inicial tem que ficar no começo. Se nenhum
    passa tão perto, vale o mais perto de todos.
    """
    melhor = (0.0, float("inf"), desde)
    primeiro = None
    kx = math.cos(math.radians(p[0])) * 111320.0
    ky = 110540.0
    for i in range(desde, len(trajeto) - 1):
        a, b = trajeto[i], trajeto[i + 1]
        ax, ay = (a[1] - p[1]) * kx, (a[0] - p[0]) * ky
        bx, by = (b[1] - p[1]) * kx, (b[0] - p[0]) * ky
        dx, dy = bx - ax, by - ay
        l2 = dx * dx + dy * dy
        t = 0.0 if l2 == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / l2))
        cx, cy = ax + t * dx, ay + t * dy
        afast = math.hypot(cx, cy)
        seg = acum[i + 1] - acum[i]
        aqui = (acum[i] + t * seg, afast, i)
        if afast < melhor[1]:
            melhor = aqui
        if afast <= perto:
            if primeiro is None or afast < primeiro[1]:
                primeiro = aqui
        elif primeiro is not None:
            return primeiro  # saiu de perto: fica o primeiro trecho que passou perto
    return primeiro if primeiro is not None else melhor


def gerar(cidade):
    cat = json.load(open(os.path.join(PASTA, "catalogo.json"), encoding="utf-8"))
    linhas = cat["cidades"][cidade]["linhas"]
    pontos, indice = [], {}
    sentidos = []
    for num in sorted(linhas):
        info = linhas[num]
        sents = info.get("sentidos", [])
        for s in sents:
            arq = os.path.join(PASTA, s["arquivo"])
            if not os.path.exists(arq):
                continue
            j = json.load(open(arq, encoding="utf-8"))
            t = [(float(p[0]), float(p[1])) for p in j.get("trajeto", [])]
            if len(t) < 2:
                continue
            acum = [0.0]
            for i in range(1, len(t)):
                acum.append(acum[-1] + distancia(t[i - 1], t[i]))
            ps, ms, desde = [], [], 0
            for par in j.get("paradas", []):
                p = (float(par["lat"]), float(par["lng"]))
                m, afast, i = projetar(p, t, acum, desde)
                if afast > 60:  # fora de ordem: procura no trajeto todo (como o app)
                    m, afast, i = projetar(p, t, acum, 0)
                desde = i
                chave = (round(p[0], 5), round(p[1], 5))
                k = indice.get(chave)
                if k is None:
                    k = len(pontos)
                    indice[chave] = k
                    pontos.append([chave[0], chave[1], str(par.get("nome", "")).strip()])
                ps.append(k)
                ms.append(int(round(m)))
            if len(ps) < 2:
                continue
            sentidos.append({
                "id": s["id"],
                "l": num,
                "n": info.get("nome", ""),
                "d": s.get("destino", ""),
                "c": 1 if len(sents) == 1 else 0,
                "p": ps,
                "m": ms,
            })
    # a data do catálogo (e não "agora"): sem mudança nas linhas, o arquivo
    # sai igualzinho e o robô não precisa salvar nada
    return {
        "formato": 1,
        "cidade": cidade,
        "gerado": cat.get("atualizado", ""),
        "pontos": pontos,
        "sentidos": sentidos,
    }


def main():
    ap = argparse.ArgumentParser(description="Gera <cidade>/rede.json para o 'Vamos para onde?'.")
    ap.add_argument("--cidade", default="rio")
    a = ap.parse_args()
    rede = gerar(a.cidade)
    saida = os.path.join(PASTA, a.cidade, "rede.json")
    with open(saida, "w", encoding="utf-8") as f:
        json.dump(rede, f, ensure_ascii=False, separators=(",", ":"))
    print(f"{saida}: {len(rede['sentidos'])} sentidos, {len(rede['pontos'])} pontos, "
          f"{os.path.getsize(saida) / 1024:.0f} KB")


if __name__ == "__main__":
    main()
