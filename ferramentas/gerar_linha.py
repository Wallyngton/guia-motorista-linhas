#!/usr/bin/env python3
"""
Gerador de linhas para o app "Guia do Motorista".

Lê o GTFS oficial da Prefeitura do Rio (data.rio), pega o trajeto e as
paradas da linha pedida e monta um arquivo .json por sentido, com:
  - trajeto (desenho exato da linha)
  - paradas na ordem
  - curvas faladas ("vire à direita na Estrada do Itanhangá")
  - limite de velocidade de cada trecho (OpenStreetMap)
  - radares (OpenStreetMap + sua lista manual)

Uso:
    python gerar_linha.py --gtfs gtfs_rio.zip --linha 862
    python gerar_linha.py --gtfs gtfs_rio.zip --linha 862 --radares meus_radares.csv
    python gerar_linha.py --gtfs gtfs_rio.zip --linha 862 --limite-onibus 60

Os arquivos saem em ../assets/routes/ (prontos para o app) e também podem
ser enviados para o celular e importados pelo botão "Importar linha".
Só usa a biblioteca padrão do Python (não precisa instalar nada).
"""
import argparse
import csv
import hashlib
import io
import json
import math
import os
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
import zipfile
from collections import Counter, defaultdict

OSRM_URL = "https://router.project-osrm.org/route/v1/driving/"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
# servidores públicos alternativos do Overpass (usados quando o principal está ocupado)
OVERPASS_ESPELHOS = [
    OVERPASS_URL,
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]
USER_AGENT = "GuiaMotoristaOnibus/1.0 (uso pessoal)"

# ---------------------------------------------------------------- geometria

R_TERRA = 6371000.0


def dist(a, b):
    """Distância em metros entre (lat, lng) a e b."""
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    dla, dlo = la2 - la1, lo2 - lo1
    h = math.sin(dla / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin(dlo / 2) ** 2
    return 2 * R_TERRA * math.asin(math.sqrt(h))


def rumo(a, b):
    """Direção de a para b em graus (0 = norte, 90 = leste)."""
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    y = math.sin(lo2 - lo1) * math.cos(la2)
    x = math.cos(la1) * math.sin(la2) - math.sin(la1) * math.cos(la2) * math.cos(lo2 - lo1)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def acumulado(pts):
    out = [0.0]
    for i in range(1, len(pts)):
        out.append(out[-1] + dist(pts[i - 1], pts[i]))
    return out


def _proj_segmento(p, a, b):
    """Projeta p no segmento a-b (plano local). Retorna (t 0..1, distância m)."""
    k = math.cos(math.radians(p[0]))
    ax, ay = a[1] * k, a[0]
    bx, by = b[1] * k, b[0]
    px, py = p[1] * k, p[0]
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
    q = (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
    return t, dist(p, q)


def posicao_na_rota(p, pts, cum, inicio=0, fim=None):
    """Distância ao longo da rota (m) do ponto mais próximo de p, e o afastamento."""
    fim = len(pts) - 1 if fim is None else min(fim, len(pts) - 1)
    melhor = (float("inf"), 0.0, inicio)
    for i in range(inicio, fim):
        t, d = _proj_segmento(p, pts[i], pts[i + 1])
        if d < melhor[0]:
            melhor = (d, cum[i] + t * (cum[i + 1] - cum[i]), i)
    return melhor[1], melhor[0], melhor[2]


def reamostrar(pts, passo):
    """Pontos a cada `passo` metros ao longo da linha (inclui início e fim)."""
    cum = acumulado(pts)
    out, alvo, i = [pts[0]], passo, 0
    while alvo < cum[-1]:
        while cum[i + 1] < alvo:
            i += 1
        seg = cum[i + 1] - cum[i]
        t = 0 if seg == 0 else (alvo - cum[i]) / seg
        out.append((pts[i][0] + (pts[i + 1][0] - pts[i][0]) * t,
                    pts[i][1] + (pts[i + 1][1] - pts[i][1]) * t))
        alvo += passo
    out.append(pts[-1])
    return out


# ---------------------------------------------------------------- GTFS

def ler_csv(z, nome):
    with z.open(nome) as f:
        return list(csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig")))


def normalizar_linha(n):
    return "".join(str(n).split()).upper()


def ler_gtfs(caminho_zip, linhas=None):
    """Lê o GTFS uma vez só. `linhas` = conjunto de números (ou None = todas).
    Retorna {numero: (nome_longo, [sentidos...])}."""
    z = zipfile.ZipFile(caminho_zip)
    alvo = {normalizar_linha(x) for x in linhas} if linhas else None

    rotas_por_num = defaultdict(list)
    for r in ler_csv(z, "routes.txt"):
        num = normalizar_linha(r.get("route_short_name", ""))
        if num and (alvo is None or num in alvo):
            rotas_por_num[num].append(r)
    route_para_num = {r["route_id"]: num for num, rs in rotas_por_num.items() for r in rs}

    viagens = defaultdict(lambda: defaultdict(list))  # num -> sentido -> trips
    for t in ler_csv(z, "trips.txt"):
        num = route_para_num.get(t["route_id"])
        if num:
            viagens[num][t.get("direction_id", "0") or "0"].append(t)

    # Para cada sentido, o desenho (shape) mais usado = trajeto principal.
    escolhidas = {}
    for num, sentidos in viagens.items():
        for sentido, ts in sentidos.items():
            shape = Counter(t.get("shape_id", "") for t in ts).most_common(1)[0][0]
            escolhidas[(num, sentido)] = next(t for t in ts if t.get("shape_id", "") == shape)

    trip_ids = {v["trip_id"] for v in escolhidas.values()}
    shape_ids = {v["shape_id"] for v in escolhidas.values()}

    paradas_por_trip = defaultdict(list)
    with z.open("stop_times.txt") as f:
        for r in csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig")):
            if r["trip_id"] in trip_ids:
                paradas_por_trip[r["trip_id"]].append((int(r["stop_sequence"]), r["stop_id"]))

    stops = {s["stop_id"]: s for s in ler_csv(z, "stops.txt")}

    shapes = defaultdict(list)
    with z.open("shapes.txt") as f:
        for r in csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig")):
            if r["shape_id"] in shape_ids:
                shapes[r["shape_id"]].append(
                    (int(r["shape_pt_sequence"]), float(r["shape_pt_lat"]), float(r["shape_pt_lon"])))

    resultado = {}
    for num in sorted(rotas_por_num):
        sentidos = []
        for (n, sentido), v in sorted(escolhidas.items()):
            if n != num:
                continue
            pts = [(la, lo) for _, la, lo in sorted(shapes.get(v["shape_id"], []))]
            if len(pts) < 2:
                continue
            limpo = [pts[0]]
            for p in pts[1:]:
                if dist(p, limpo[-1]) > 0.5:
                    limpo.append(p)
            paradas = []
            for _, sid in sorted(paradas_por_trip[v["trip_id"]]):
                st = stops.get(sid)
                if st:
                    paradas.append({"nome": st.get("stop_name", "Parada").strip() or "Parada",
                                    "lat": float(st["stop_lat"]), "lng": float(st["stop_lon"])})
            sentidos.append({"sentido": sentido, "destino": v.get("trip_headsign", "").strip(),
                             "trajeto": limpo, "paradas": paradas})
        if sentidos:
            resultado[num] = (rotas_por_num[num][0].get("route_long_name", ""), sentidos)
    return resultado


# ---------------------------------------------------------------- empresas (CSV)

def sem_acento(s):
    """'Viação  Redentor' → 'viacao redentor' (para comparar nomes)."""
    s = unicodedata.normalize("NFD", str(s or ""))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return " ".join(s.lower().replace("\ufeff", "").split())


# nomes aceitos no cabeçalho do CSV (sem acento, minúsculas)
COLUNAS_LINHA = {"linha", "linhas", "numero", "numero da linha", "n", "no", "n.", "n°", "nº", "n o",
                 "line", "route", "route_short_name", "servico", "codigo", "cod", "linha de onibus"}
COLUNAS_EMPRESA = {"empresa", "empresas", "operadora", "operador", "viacao", "nome da empresa",
                   "empresa operadora", "agency", "agency_name", "concessionaria", "nome"}


def _ler_texto(caminho):
    """Lê o arquivo de texto em qualquer codificação comum no Windows:
    UTF-8 (com ou sem BOM), UTF-16 ("Texto Unicode" do Excel) e cp1252
    (o "CSV" do Excel em português)."""
    with open(caminho, "rb") as f:
        dados = f.read()
    if dados.startswith((b"\xff\xfe", b"\xfe\xff")):
        return dados.decode("utf-16"), "utf-16"
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return dados.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return dados.decode("latin-1", errors="replace"), "latin-1"


def _separador(texto):
    """Descobre o separador olhando a primeira linha com conteúdo:
    ';' (Excel em português), ',' ou TAB."""
    for linha in texto.splitlines():
        if linha.strip() and not linha.lstrip().startswith("#"):
            contagem = {d: linha.count(d) for d in (";", ",", "\t")}
            melhor = max(contagem, key=lambda d: (contagem[d], d == ","))
            return melhor if contagem[melhor] else ","
    return ","


def _limpar_numero(valor):
    num = normalizar_linha(valor).strip('"').strip("'")
    if re.fullmatch(r"\d+[.,]0+", num):  # Excel às vezes salva 862 como 862.0
        num = re.split(r"[.,]", num)[0]
    return num


def ler_empresas_csv(caminho):
    """Lê o CSV linha → empresa, aceitando o que o Excel salva.
    Retorna (dict {linha: empresa}, lista de avisos)."""
    texto, enc = _ler_texto(caminho)
    sep = _separador(texto)
    avisos = []
    linhas = [[c.strip() for c in r] for r in csv.reader(io.StringIO(texto), delimiter=sep)]
    linhas = [r for r in linhas if any(r) and not r[0].lstrip().startswith("#")]
    if not linhas:
        return {}, [f"{os.path.basename(caminho)} está vazio."]
    cab = [sem_acento(c).strip(" :") for c in linhas[0]]
    i_lin = next((i for i, c in enumerate(cab) if c in COLUNAS_LINHA), None)
    i_emp = next((i for i, c in enumerate(cab) if c in COLUNAS_EMPRESA and i != i_lin), None)
    dados = linhas[1:]
    if i_lin is None and i_emp is None:
        if re.search(r"\d", linhas[0][0]):
            dados = linhas  # sem cabeçalho: 1ª coluna = linha, 2ª = empresa
        else:
            avisos.append(f"Cabeçalho não reconhecido ({sep.join(linhas[0])}); "
                          "usando 1ª coluna = linha e 2ª = empresa.")
        i_lin, i_emp = 0, 1
    elif i_lin is None:
        i_lin = 0 if i_emp != 0 else 1
    elif i_emp is None:
        i_emp = 1 if i_lin != 1 else 0
    out = {}
    for r in dados:
        if len(r) <= max(i_lin, i_emp):
            continue
        num, emp = _limpar_numero(r[i_lin]), " ".join(r[i_emp].split())
        if num and emp:
            out[num] = emp
    nome_sep = {";": "ponto e vírgula", ",": "vírgula", "\t": "TAB"}[sep]
    avisos.insert(0, f"{os.path.basename(caminho)}: {len(out)} linha(s) lidas "
                     f"(separador {nome_sep}, codificação {enc}).")
    return out, avisos


def casar_numero(num, conhecidas):
    """'6' no CSV (o Excel tira o zero) acha '006' da central."""
    if num in conhecidas:
        return num
    alvo = num.lstrip("0") or "0"
    cands = [k for k in conhecidas if (k.lstrip("0") or "0") == alvo]
    return cands[0] if len(cands) == 1 else None


def empresa_com_consorcio(empresa, atual):
    """Junta a empresa do CSV com o consórcio que já estava (GTFS):
    'Viação Redentor' + 'Transcarioca' → 'Viação Redentor (Transcarioca)'.
    Pode rodar quantas vezes quiser: não repete o consórcio."""
    atual = (atual or "").strip()
    m = re.fullmatch(r"(.*\S)\s*\(([^()]+)\)", atual)
    cons = m.group(2).strip() if m else atual
    if not cons or sem_acento(cons) in sem_acento(empresa) or sem_acento(empresa) == sem_acento(cons):
        return empresa
    return f"{empresa} ({cons})"


def empresas_das_linhas(caminho_zip, csv_extra=None, log=print):
    """Empresa (operadora) de cada linha: vem do agency.txt do GTFS.
    `csv_extra` (opcional, colunas linha,empresa) corrige ou completa —
    útil quando o GTFS traz o consórcio e não a empresa que opera a linha.
    Retorna (dict {linha: empresa}, quantas vieram do CSV)."""
    z = zipfile.ZipFile(caminho_zip)
    nomes = {}
    if "agency.txt" in z.namelist():
        for a in ler_csv(z, "agency.txt"):
            nomes[a.get("agency_id", "")] = a.get("agency_name", "").strip()
    out = {}
    for r in ler_csv(z, "routes.txt"):
        num = normalizar_linha(r.get("route_short_name", ""))
        if not num:
            continue
        emp = nomes.get(r.get("agency_id", ""), "") or (next(iter(nomes.values())) if len(nomes) == 1 else "")
        if emp:
            out.setdefault(num, set()).add(emp)
    res = {k: " / ".join(sorted(v)) for k, v in out.items()}
    do_csv = 0
    if csv_extra:
        lidas, avisos = ler_empresas_csv(csv_extra)
        for a in avisos:
            log(a)
        conhecidas = set(res)
        nao_achadas = []
        for num_csv, emp in lidas.items():
            num = casar_numero(num_csv, conhecidas)
            if num is None:
                nao_achadas.append(num_csv)
                num = num_csv
            else:
                do_csv += 1
            # guarda também o consórcio: a busca acha pelos dois nomes
            # (ex.: "Viação Redentor (Transcarioca)")
            res[num] = empresa_com_consorcio(emp, res.get(num, ""))
        if nao_achadas:
            log(f"  Atenção: {len(nao_achadas)} linha(s) do CSV não estão no GTFS: "
                f"{', '.join(sorted(nao_achadas)[:20])}")
    return res, do_csv


# ---------------------------------------------------------------- mudou a linha?

def _assinatura(nome_longo, partes):
    texto = json.dumps([nome_longo or "", sorted(partes, key=lambda p: p[0])],
                       ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha1(texto.encode("utf-8")).hexdigest()[:16]


def impressao_gtfs(nome_longo, sentidos):
    """'Impressão digital' do que vem do GTFS para uma linha (nome, destinos,
    trajeto e paradas). Se for igual à da última vez, a linha não mudou e
    não precisa ser gerada de novo (economiza o OpenStreetMap)."""
    partes = []
    for s in sentidos:
        partes.append([str(s["sentido"]), s["destino"] or f"sentido {s['sentido']}",
                       [[round(a, 6), round(b, 6)] for a, b in s["trajeto"]],
                       [[p["nome"], p["lat"], p["lng"]] for p in s["paradas"]]])
    return _assinatura(nome_longo, partes)


def impressao_arquivos(rotas):
    """Mesma impressão digital, calculada dos arquivos já gerados
    (para catálogos antigos, que ainda não guardam a impressão)."""
    partes, nome = [], ""
    for r in rotas:
        sentido = str(r["id"])[len(str(r["linha"])) + 1:]
        nome = r.get("descricao", "")
        partes.append([sentido, r.get("destino", ""),
                       [[a, b] for a, b in r.get("trajeto", [])],
                       [[p["nome"], p["lat"], p["lng"]] for p in r.get("paradas", [])]])
    return _assinatura(nome, partes)


def ler_linha_gtfs(caminho_zip, linha):
    tudo = ler_gtfs(caminho_zip, {linha})
    num = normalizar_linha(linha)
    if num not in tudo:
        sys.exit(f"Linha {linha} não encontrada no GTFS (ou sem viagens).")
    nome_longo, sentidos = tudo[num]
    print(f"Linha {num}: {nome_longo}")
    return nome_longo, sentidos


# ---------------------------------------------------------------- internet

def baixar(url, dados=None, tentativas=3):
    for n in range(tentativas):
        try:
            req = urllib.request.Request(url, data=dados, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=180) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            if n == tentativas - 1:
                raise
            print(f"  (falhou: {e}; tentando de novo...)")
            time.sleep(3 * (n + 1))


# ---------------------------------------------------------------- curvas

MODIFICADOR = {
    "right": "vire à direita", "slight right": "mantenha-se à direita",
    "sharp right": "vire fortemente à direita", "left": "vire à esquerda",
    "slight left": "mantenha-se à esquerda", "sharp left": "vire fortemente à esquerda",
    "uturn": "faça o retorno", "straight": "siga em frente",
}


MASCULINOS = ("largo", "viaduto", "túnel", "tunel", "elevado", "acesso", "boulevard",
              "caminho", "corredor", "mergulhão", "mergulhao", "trevo", "beco", "anel")


def em_rua(rua):
    """' na Rua X' / ' no Viaduto Y' / '' """
    if not rua:
        return ""
    primeira = rua.split()[0].lower().strip(".")
    return f" {'no' if primeira in MASCULINOS else 'na'} {rua}"


def texto_manobra(tipo, mod, rua, saida=None):
    em = em_rua(rua)
    if tipo == "roundabout" or tipo == "rotary":
        n = f"pegue a {saida}ª saída" if saida else "siga pela rotatória"
        return f"Na rotatória, {n}{em}"
    if tipo in ("fork",):
        lado = "à direita" if "right" in (mod or "") else "à esquerda"
        return f"Na bifurcação, mantenha-se {lado}{em}"
    if tipo in ("on ramp",):
        return f"Pegue o acesso {'à direita' if 'right' in (mod or '') else 'à esquerda'}{em}"
    if tipo in ("off ramp",):
        return f"Pegue a saída {'à direita' if 'right' in (mod or '') else 'à esquerda'}{em}"
    frase = MODIFICADOR.get(mod or "straight", "siga")
    return frase[0].upper() + frase[1:] + em


def manobras_osrm(trajeto):
    """Pede ao OSRM as curvas, forçando o caminho pelos pontos da linha."""
    pontos = reamostrar(trajeto, 250)
    manobras = []
    BLOCO = 90
    i = 0
    while i < len(pontos) - 1:
        bloco = pontos[i:i + BLOCO]
        coords = ";".join(f"{lo:.6f},{la:.6f}" for la, lo in bloco)
        url = (OSRM_URL + coords + "?steps=true&overview=false&continue_straight=true"
               "&geometries=geojson")
        dados = baixar(url)
        if dados.get("code") != "Ok":
            raise RuntimeError(dados.get("message", "OSRM sem resposta"))
        rua_anterior = None
        for leg in dados["routes"][0]["legs"]:
            for st in leg["steps"]:
                m = st["maneuver"]
                tipo, mod = m["type"], m.get("modifier")
                rua = (st.get("name") or "").strip()
                if tipo in ("depart", "arrive"):
                    rua_anterior = rua or rua_anterior
                    continue
                if tipo in ("new name", "continue") and mod in (None, "straight", "slight left", "slight right"):
                    rua_anterior = rua or rua_anterior
                    continue
                if mod == "straight" and tipo not in ("roundabout", "rotary"):
                    continue
                lo, la = m["location"]
                manobras.append({"lat": la, "lng": lo,
                                 "texto": texto_manobra(tipo, mod, rua, m.get("exit"))})
                rua_anterior = rua
        i += BLOCO - 1
        time.sleep(1)  # respeita o servidor público
    # remove duplicadas (blocos se sobrepõem)
    unicas = []
    for m in manobras:
        if not unicas or dist((m["lat"], m["lng"]), (unicas[-1]["lat"], unicas[-1]["lng"])) > 25:
            unicas.append(m)
    return unicas


def manobras_geometricas(trajeto, ruas=None):
    """Acha as curvas pelo desenho da rota. Com `ruas` (vias com nome do
    OpenStreetMap), diz também o nome da rua em que se entra."""
    indice = IndiceVias(ruas) if ruas else None
    pts = reamostrar(trajeto, 15)
    giros = [0.0] * len(pts)
    for i in range(3, len(pts) - 3):
        antes = rumo(pts[i - 3], pts[i])
        depois = rumo(pts[i], pts[i + 3])
        giros[i] = (depois - antes + 540) % 360 - 180
    # agrupa pontos seguidos com giro forte e fica com o pico de cada grupo
    out, i = [], 0
    while i < len(pts):
        if abs(giros[i]) < 40:
            i += 1
            continue
        j = i
        while j + 1 < len(pts) and abs(giros[j + 1]) >= 40 and (giros[j + 1] > 0) == (giros[i] > 0):
            j += 1
        k = max(range(i, j + 1), key=lambda n: abs(giros[n]))
        g = giros[k]
        if abs(g) > 150:
            txt = "Faça o retorno"
        elif g > 0:
            txt = "Vire à direita" if g > 60 else "Mantenha-se à direita"
        else:
            txt = "Vire à esquerda" if g < -60 else "Mantenha-se à esquerda"
        if indice and txt != "Faça o retorno":
            # olha ~45 m depois da curva para saber em que rua entrou
            d = min(k + 3, len(pts) - 2)
            via = indice.proxima(pts[d], rumo(pts[d], pts[d + 1]), max_dist=25)
            if via and via.get("nome"):
                txt += em_rua(via["nome"])
        out.append({"lat": pts[k][0], "lng": pts[k][1], "texto": txt})
        i = j + 1
    return out


# ---------------------------------------------------------------- velocidade e radares

def kmh(valor):
    if not valor:
        return None
    v = valor.split(";")[0].strip().lower()
    try:
        if "mph" in v:
            return round(float(v.replace("mph", "").strip()) * 1.609)
        return int(float(v.split()[0]))
    except ValueError:
        return None


def dados_osm(trajeto):
    las = [p[0] for p in trajeto]
    los = [p[1] for p in trajeto]
    m = 0.003
    bbox = f"{min(las)-m},{min(los)-m},{max(las)+m},{max(los)+m}"
    q = f"""[out:json][timeout:180];
(
  way["highway"]["maxspeed"]({bbox});
  way["highway"]["maxspeed:bus"]({bbox});
  way["highway"~"^(motorway|trunk|primary|secondary|tertiary|unclassified|residential|living_street|service|busway)(_link)?$"]["name"]({bbox});
  node["highway"="speed_camera"]({bbox});
  node["enforcement"="maxspeed"]({bbox});
);
out tags geom;"""
    dados = urllib.parse.urlencode({"data": q}).encode()
    erro = None
    for rodada in range(2):  # 2 voltas por todos os servidores
        for url in OVERPASS_ESPELHOS:
            try:
                return baixar(url, dados, tentativas=2)
            except Exception as e:  # noqa: BLE001
                erro = e
                print(f"  (servidor {url.split('/')[2]} ocupado; tentando outro...)")
                time.sleep(5 + 10 * rodada)
    raise erro


class IndiceVias:
    """Acha rapidamente a via (do OpenStreetMap) mais próxima de um ponto,
    andando na mesma direção da rota (ignora ruas que só cruzam)."""
    CEL = 0.002

    def __init__(self, vias):
        self.vias = vias
        self.grade = defaultdict(list)
        for vi, via in enumerate(vias):
            pts = via["pts"]
            if len(pts) > 1:
                pts = reamostrar(pts, 50)  # cobre trechos longos entre vértices
            for la, lo in pts:
                self.grade[(int(la / self.CEL), int(lo / self.CEL))].append(vi)

    def proxima(self, p, rumo_rota, max_dist=20.0):
        cands = set()
        gx, gy = int(p[0] / self.CEL), int(p[1] / self.CEL)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                cands.update(self.grade.get((gx + dx, gy + dy), ()))
        melhor, melhor_d = None, max_dist
        for vi in cands:
            vp = self.vias[vi]["pts"]
            for k in range(len(vp) - 1):
                _, d = _proj_segmento(p, vp[k], vp[k + 1])
                if d < melhor_d:
                    r = rumo(vp[k], vp[k + 1])
                    dif = abs((r - rumo_rota + 540) % 360 - 180)
                    if dif < 35 or dif > 145:
                        melhor, melhor_d = vi, d
        return None if melhor is None else self.vias[melhor]


def velocidades_por_trecho(trajeto, cum, vias, limite_onibus=None):
    """Para cada pedaço de ~25 m da rota, acha a via mais próxima e seu limite."""
    indice = IndiceVias(vias)

    trechos = []
    passo = 25.0
    s = 0.0
    i = 0
    while s < cum[-1]:
        while cum[i + 1] < s:
            i += 1
        seg = cum[i + 1] - cum[i]
        t = 0 if seg == 0 else (s - cum[i]) / seg
        p = (trajeto[i][0] + (trajeto[i + 1][0] - trajeto[i][0]) * t,
             trajeto[i][1] + (trajeto[i + 1][1] - trajeto[i][1]) * t)
        via = indice.proxima(p, rumo(trajeto[i], trajeto[i + 1]))
        v = via["kmh"] if via else None
        if v and limite_onibus:
            v = min(v, limite_onibus)
        elif not v and limite_onibus:
            v = None  # sem dado da via: não inventa limite
        trechos.append((s, v))
        s += passo

    # junta trechos iguais em faixas
    faixas = []
    for s, v in trechos:
        if faixas and faixas[-1]["kmh"] == v:
            faixas[-1]["fim_m"] = round(s + passo, 1)
        else:
            faixas.append({"inicio_m": round(s, 1), "fim_m": round(s + passo, 1), "kmh": v})
    faixas[-1]["fim_m"] = round(cum[-1], 1)
    return [f for f in faixas if f["kmh"]]


def ler_radares_manuais(caminho):
    """CSV com colunas: lat,lng,kmh,descricao (descricao opcional)."""
    out = []
    with open(caminho, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            try:
                out.append({"lat": float(r["lat"]), "lng": float(r["lng"]),
                            "kmh": int(r["kmh"]) if r.get("kmh") else None,
                            "descricao": (r.get("descricao") or "").strip(),
                            "fonte": "manual"})
            except (KeyError, ValueError):
                print(f"  linha ignorada no CSV de radares: {r}")
    return out


# ---------------------------------------------------------------- montagem

def separar_osm(osm):
    """Divide a resposta do OpenStreetMap em: vias com limite, ruas com nome, radares."""
    vias, ruas, radares = [], [], []
    for el in (osm or {}).get("elements", []):
        tg = el.get("tags", {})
        if el["type"] == "way" and "geometry" in el:
            pts = [(g["lat"], g["lon"]) for g in el["geometry"]]
            v = kmh(tg.get("maxspeed:bus")) or kmh(tg.get("maxspeed"))
            if v:
                vias.append({"kmh": v, "pts": pts})
            if tg.get("name"):
                ruas.append({"nome": tg["name"], "pts": pts})
        elif el["type"] == "node":
            radares.append({"lat": el["lat"], "lng": el["lon"],
                            "kmh": kmh(tg.get("maxspeed")), "descricao": tg.get("name", ""),
                            "fonte": "OpenStreetMap"})
    return vias, ruas, radares


def montar(sentido_info, linha, nome_longo, osm, radares_manuais=(), limite_onibus=None,
           usar_osrm=False, cidade="", log=print):
    trajeto = sentido_info["trajeto"]
    cum = acumulado(trajeto)
    log(f"  Trajeto: {cum[-1]/1000:.1f} km, {len(sentido_info['paradas'])} paradas")

    vias, ruas, radares = separar_osm(osm)

    manobras = None
    if usar_osrm:
        try:
            log("  Buscando curvas com nome de rua (OSRM)...")
            manobras = manobras_osrm(trajeto)
        except Exception as e:  # noqa: BLE001
            log(f"  OSRM indisponível ({e}). Usando curvas pelo desenho da rota.")
    if manobras is None:
        manobras = manobras_geometricas(trajeto, ruas)
    log(f"  Curvas: {len(manobras)}")

    radares = radares + list(radares_manuais)
    velocidades = velocidades_por_trecho(trajeto, cum, vias, limite_onibus) if vias else []
    log(f"  Trechos com limite de velocidade: {len(velocidades)}")

    # só mantém radares que ficam em cima da rota (até 30 m)
    perto = []
    for r in radares:
        s, d, _ = posicao_na_rota((r["lat"], r["lng"]), trajeto, cum)
        if d <= 30:
            r = dict(r)
            if not r.get("kmh"):
                r["kmh"] = next((f["kmh"] for f in velocidades if f["inicio_m"] <= s < f["fim_m"]), None)
            perto.append(r)
    log(f"  Radares na rota: {len(perto)}")

    destino = sentido_info["destino"] or f"sentido {sentido_info['sentido']}"
    return {
        "formato": 1,
        "id": f"{linha}-{sentido_info['sentido']}",
        "linha": linha,
        "cidade": cidade,
        "nome": f"{linha} → {destino}",
        "destino": destino,
        "descricao": nome_longo,
        "gerado_em": time.strftime("%Y-%m-%d"),
        "fonte": "GTFS da Prefeitura, OpenStreetMap" + (", OSRM" if usar_osrm else ""),
        "trajeto": [[round(a, 6), round(b, 6)] for a, b in trajeto],
        "paradas": sentido_info["paradas"],
        "manobras": manobras,
        "velocidades": velocidades,
        "radares": perto,
    }


def main():
    ap = argparse.ArgumentParser(description="Gera o arquivo de uma linha de ônibus para o app.")
    ap.add_argument("--gtfs", required=True, help="arquivo .zip do GTFS (data.rio)")
    ap.add_argument("--linha", required=True, help="número da linha, ex.: 862")
    ap.add_argument("--radares", help="CSV com radares conhecidos (lat,lng,kmh,descricao)")
    ap.add_argument("--limite-onibus", type=int,
                    help="velocidade máxima da empresa para ônibus (km/h); o app usa o menor valor")
    ap.add_argument("--sem-internet", action="store_true",
                    help="não consulta OSRM/OpenStreetMap (sem nomes de rua, limites e radares)")
    ap.add_argument("--saida", default=os.path.join(os.path.dirname(__file__), "..", "assets", "routes"))
    a = ap.parse_args()

    linha = normalizar_linha(a.linha)
    nome_longo, sentidos = ler_linha_gtfs(a.gtfs, linha)
    manuais = ler_radares_manuais(a.radares) if a.radares else []

    osm = None
    if not a.sem_internet:
        try:
            todos = [p for s in sentidos for p in s["trajeto"]]
            print("Buscando limites de velocidade e radares (OpenStreetMap)...")
            osm = dados_osm(todos)
        except Exception as e:  # noqa: BLE001
            print(f"OpenStreetMap indisponível ({e}). Seguindo sem limites/radares automáticos.")

    os.makedirs(a.saida, exist_ok=True)
    for s in sentidos:
        print(f"Sentido {s['sentido']} → {s['destino']}")
        rota = montar(s, linha, nome_longo, osm, manuais, a.limite_onibus,
                      usar_osrm=not a.sem_internet)
        caminho = os.path.join(a.saida, f"{rota['id']}.json")
        with open(caminho, "w", encoding="utf-8") as f:
            json.dump(rota, f, ensure_ascii=False, separators=(",", ":"))
        print(f"  Salvo: {os.path.abspath(caminho)}")
    print("Pronto!")


if __name__ == "__main__":
    main()
