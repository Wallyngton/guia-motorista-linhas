#!/usr/bin/env python3
"""
Gera a CENTRAL de linhas: os arquivos que o app baixa quando o motorista
digita o número da linha na tela inicial.

    python gerar_central.py --gtfs gtfs_rio.zip --cidade rio --nome-cidade "Rio de Janeiro"

Cria (ou atualiza):
    ../central/catalogo.json        lista de cidades e linhas (o app lê primeiro)
    ../central/rio/862-0.json       cada sentido de cada linha
    ../central/rio/862-1.json
    ...

Depois é só publicar a pasta `central` num endereço da internet
(ex.: GitHub Pages, de graça — veja o LEIA-ME) e colocar esse endereço
em lib/config.dart. Para atualizar as rotas, rode de novo com um GTFS
mais novo e publique outra vez: o gerador refaz SÓ as linhas que mudaram
(trajeto, paradas, destino ou nome) e os celulares baixam essas linhas.

Opções:
    --linhas 862,309      só essas linhas (bom para testar)
    --sem-internet        não consulta o OpenStreetMap (sem nomes de rua,
                          limites e radares) — bem mais rápido
    --refazer             gera de novo TODAS as linhas, mesmo sem mudança
    --refazer-antes 2026-10-01   gera de novo as linhas geradas antes dessa data
    --tempo-max 300       para depois de 300 minutos (salva tudo; rode de novo
                          que continua de onde parou)
    --resumo resumo.json  grava um resumo (geradas, faltam...) para automação
    --limite-onibus 60    limite máximo da frota (km/h)
    --empresas empresas.csv   empresa de cada linha (colunas: linha,empresa),
                          quando o GTFS traz só o consórcio — é o que o app
                          usa na busca "linhas da empresa" (Correr linha).
                          Aceita o CSV do Excel (com ; e acentos do Windows).

Só corrigir as empresas (sem GTFS, sem refazer nada, leva segundos):
    python gerar_central.py --so-empresas
    python gerar_central.py --so-empresas --saida C:\\caminho\\da\\central --empresas empresas.csv
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gerar_linha import (dados_osm, ler_gtfs, montar, normalizar_linha,  # noqa: E402
                         acumulado, empresas_das_linhas, ler_empresas_csv, casar_numero,
                         empresa_com_consorcio, impressao_gtfs, impressao_arquivos)

PASTA_SCRIPT = os.path.dirname(os.path.abspath(__file__))


def carregar_catalogo(caminho):
    if os.path.exists(caminho):
        with open(caminho, encoding="utf-8-sig") as f:
            return json.load(f)
    return {"formato": 1, "atualizado": "", "cidades": {}}


def salvar_json(caminho, dados, compacto=True):
    tmp = caminho + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        if compacto:
            json.dump(dados, f, ensure_ascii=False, separators=(",", ":"))
        else:
            json.dump(dados, f, ensure_ascii=False, indent=1)
    os.replace(tmp, caminho)


def aplicar_empresas(catalogo, csv_path, cidade=None, log=print):
    """Põe a empresa do CSV nas linhas do catálogo já gerado, sem refazer
    nada. Mantém o consórcio junto: "Viação Redentor (Transcarioca)".
    Retorna quantas linhas receberam a empresa do CSV."""
    lidas, avisos = ler_empresas_csv(csv_path)
    for a in avisos:
        log(a)
    cidades = catalogo.get("cidades", {})
    alvo = [cidade] if cidade else list(cidades)
    aplicadas, nao_achadas = 0, []
    for num_csv, emp in sorted(lidas.items()):
        achou = False
        for c in alvo:
            linhas = cidades.get(c, {}).get("linhas", {})
            num = casar_numero(num_csv, set(linhas))
            if num is None:
                continue
            achou = True
            ent = linhas[num]
            novo = empresa_com_consorcio(emp, ent.get("empresa", ""))
            if ent.get("empresa") != novo:
                log(f"  {num}: {ent.get('empresa', '') or '(sem empresa)'} → {novo}")
            ent["empresa"] = novo
            aplicadas += 1
        if not achou:
            nao_achadas.append(num_csv)
    if nao_achadas:
        log(f"  Atenção: {len(nao_achadas)} linha(s) do CSV não estão na central: "
            f"{', '.join(nao_achadas[:20])}")
    return aplicadas


def so_empresas(a):
    cat_path = os.path.join(a.saida, "catalogo.json")
    if not os.path.exists(cat_path):
        sys.exit(f"Não achei {os.path.abspath(cat_path)}.\n"
                 "Use --saida com a pasta da central (a que tem o catalogo.json).")
    if not a.empresas or not os.path.exists(a.empresas):
        sys.exit("Não achei o empresas.csv. Use --empresas caminho\\empresas.csv")
    catalogo = carregar_catalogo(cat_path)
    cidade = a.cidade.strip().lower() if a.cidade else None
    n = aplicar_empresas(catalogo, a.empresas, cidade)
    catalogo["atualizado"] = time.strftime("%Y-%m-%d %H:%M")
    salvar_json(cat_path, catalogo, compacto=False)
    print(f"\n{n} linha(s) ficaram com a empresa do CSV.")
    print(f"Catálogo salvo: {os.path.abspath(cat_path)}")
    print("Agora envie SÓ o catalogo.json de novo para o GitHub (Add file › Upload files).")


def main():
    ap = argparse.ArgumentParser(description="Gera a central de linhas para o app.")
    ap.add_argument("--gtfs")
    ap.add_argument("--cidade", help="código curto, ex.: rio, sp, bh")
    ap.add_argument("--nome-cidade", help='ex.: "Rio de Janeiro"')
    ap.add_argument("--linhas", help="lista separada por vírgula (padrão: todas)")
    ap.add_argument("--sem-internet", action="store_true")
    ap.add_argument("--refazer", action="store_true")
    ap.add_argument("--refazer-antes", help="AAAA-MM-DD: refaz linhas geradas antes dessa data")
    ap.add_argument("--tempo-max", type=float, help="minutos; para e salva quando passar")
    ap.add_argument("--resumo", help="arquivo .json com o resumo da execução")
    ap.add_argument("--limite-onibus", type=int)
    ap.add_argument("--empresas", help="CSV linha,empresa (opcional)")
    ap.add_argument("--so-empresas", action="store_true",
                    help="só aplica o empresas.csv no catálogo que já existe (rápido)")
    ap.add_argument("--saida", default=os.path.join(PASTA_SCRIPT, "..", "central"))
    a = ap.parse_args()
    # lista linha → empresa ao lado do gerador é usada sozinha, se existir
    padrao = os.path.join(PASTA_SCRIPT, "empresas.csv")
    if not a.empresas and os.path.exists(padrao):
        a.empresas = padrao

    if a.so_empresas:
        so_empresas(a)
        return
    if not (a.gtfs and a.cidade and a.nome_cidade):
        ap.error("informe --gtfs, --cidade e --nome-cidade (ou use --so-empresas)")

    cidade = a.cidade.strip().lower()
    pasta = os.path.join(a.saida, cidade)
    os.makedirs(pasta, exist_ok=True)
    cat_path = os.path.join(a.saida, "catalogo.json")
    catalogo = carregar_catalogo(cat_path)
    cid = catalogo["cidades"].setdefault(cidade, {"nome": a.nome_cidade, "linhas": {}})
    cid["nome"] = a.nome_cidade

    filtro = {normalizar_linha(x) for x in a.linhas.split(",") if x.strip()} if a.linhas else None
    print("Lendo o GTFS (pode levar alguns minutos)...")
    todas = ler_gtfs(a.gtfs, filtro)
    empresas, do_csv = empresas_das_linhas(a.gtfs, a.empresas)
    print(f"{len(todas)} linha(s) encontradas.")
    if a.empresas:
        print(f"{do_csv} linha(s) com a empresa do {os.path.basename(a.empresas)}.")
    else:
        print("Sem empresas.csv: as linhas ficam com o nome do GTFS (no Rio, o consórcio).")
    # atualiza a empresa até das linhas que já existiam (sem refazer o resto)
    for num, ent in cid["linhas"].items():
        if empresas.get(num):
            ent["empresa"] = empresas[num]

    def sem_osm(num):
        """Linha gerada sem os dados do OpenStreetMap (servidor estava fora)?
        Essas são refeitas na próxima vez para ganhar limites e radares."""
        if a.sem_internet:
            return False
        ent = cid["linhas"].get(num) or {}
        if "osm" in ent:
            return not ent["osm"]
        # catálogos antigos (sem a marca): olha o arquivo da linha
        for s in ent.get("sentidos", []):
            try:
                with open(os.path.join(a.saida, s["arquivo"]), encoding="utf-8") as f:
                    r = json.load(f)
                if r.get("velocidades") or r.get("radares"):
                    return False
            except Exception:  # noqa: BLE001
                return True
        return True

    def impressao_guardada(num):
        """Impressão digital da última geração (do catálogo ou dos arquivos)."""
        ent = cid["linhas"].get(num) or {}
        if ent.get("hash"):
            return ent["hash"]
        try:
            rotas = []
            for s in ent.get("sentidos", []):
                with open(os.path.join(a.saida, s["arquivo"]), encoding="utf-8") as f:
                    rotas.append(json.load(f))
            return impressao_arquivos(rotas) if rotas else None
        except Exception:  # noqa: BLE001
            return None

    # 1) decide o que precisa gerar (e por quê)
    PRIORIDADE = {"mudou": 0, "nova": 1, "refazer": 2, "sem mapa": 3}
    pendentes, puladas = [], 0
    for num, (nome_longo, sentidos) in sorted(todas.items()):
        novo_hash = impressao_gtfs(nome_longo, sentidos)
        ent = cid["linhas"].get(num)
        ids = [f"{num}-{s['sentido']}" for s in sentidos]
        ja_existe = ent is not None and all(os.path.exists(os.path.join(pasta, f"{i}.json")) for i in ids)
        if not ja_existe:
            motivo = "nova"
        elif impressao_guardada(num) != novo_hash:
            motivo = "mudou"
        elif a.refazer or (a.refazer_antes and ent.get("atualizado", "") < a.refazer_antes):
            motivo = "refazer"
        elif sem_osm(num):
            motivo = "sem mapa"
        else:
            motivo = None
            ent["hash"] = novo_hash  # catálogos antigos passam a guardar a impressão
        if motivo is None:
            puladas += 1
        else:
            pendentes.append((PRIORIDADE[motivo], num, motivo, novo_hash))
    pendentes.sort()
    mudaram = [num for _, num, m, _ in pendentes if m == "mudou"]
    print(f"{puladas} linha(s) sem mudança; {len(pendentes)} para gerar "
          f"({len(mudaram)} mudaram, {sum(1 for p in pendentes if p[2] == 'nova')} novas).")
    if filtro is None:
        sumiram = sorted(set(cid["linhas"]) - set(todas))
        if sumiram:
            print(f"{len(sumiram)} linha(s) da central não estão mais no GTFS (mantidas): "
                  f"{', '.join(sumiram[:20])}")

    # 2) gera
    feitas = erros = sem_mapa = 0
    inicio = time.time()
    faltam = 0
    for n, (_, num, motivo, novo_hash) in enumerate(pendentes, 1):
        if a.tempo_max and (time.time() - inicio) / 60 > a.tempo_max:
            faltam = len(pendentes) - n + 1
            print(f"\nTempo máximo ({a.tempo_max:.0f} min) atingido: faltam {faltam} linha(s). "
                  "Rode de novo para continuar.")
            break
        nome_longo, sentidos = todas[num]
        print(f"[{n}/{len(pendentes)}] Linha {num} — {nome_longo} ({motivo})")
        try:
            osm = None
            if not a.sem_internet:
                try:
                    osm = dados_osm([p for s in sentidos for p in s["trajeto"]])
                    time.sleep(1.5)  # respeita o servidor público
                except Exception as e:  # noqa: BLE001
                    print(f"  OpenStreetMap indisponível ({e}); seguindo sem ele.")
            entrada = {"nome": nome_longo, "empresa": empresas.get(num, ""),
                       "atualizado": time.strftime("%Y-%m-%d"), "sentidos": [],
                       "osm": osm is not None or a.sem_internet, "hash": novo_hash}
            if osm is None and not a.sem_internet:
                sem_mapa += 1
            for s in sentidos:
                rota = montar(s, num, nome_longo, osm, limite_onibus=a.limite_onibus,
                              usar_osrm=False, cidade=cidade, log=lambda *_: None)
                salvar_json(os.path.join(pasta, f"{rota['id']}.json"), rota)
                entrada["sentidos"].append({
                    "id": rota["id"],
                    "arquivo": f"{cidade}/{rota['id']}.json",
                    "destino": rota["destino"],
                    "km": round(acumulado([tuple(p) for p in rota["trajeto"]])[-1] / 1000, 1),
                    "paradas": len(rota["paradas"]),
                })
            # sentido que deixou de existir no GTFS: apaga o arquivo velho
            novos = {s["arquivo"] for s in entrada["sentidos"]}
            for s in (cid["linhas"].get(num) or {}).get("sentidos", []):
                velho = os.path.join(a.saida, s.get("arquivo", ""))
                if s.get("arquivo") and s["arquivo"] not in novos and os.path.isfile(velho):
                    os.remove(velho)
            cid["linhas"][num] = entrada
            feitas += 1
            # salva o catálogo a cada linha: se parar no meio, dá para continuar
            catalogo["atualizado"] = time.strftime("%Y-%m-%d %H:%M")
            salvar_json(cat_path, catalogo, compacto=False)
        except Exception as e:  # noqa: BLE001
            erros += 1
            print(f"  ERRO na linha {num}: {e}")

    catalogo["atualizado"] = time.strftime("%Y-%m-%d %H:%M")
    salvar_json(cat_path, catalogo, compacto=False)
    min_ = (time.time() - inicio) / 60
    print(f"\nPronto em {min_:.0f} min: {feitas} geradas, {puladas} sem mudança, {erros} com erro.")
    if sem_mapa or erros:
        print(f"{sem_mapa} linha(s) ficaram sem limites/radares (servidor do mapa fora) e {erros} com erro.\n"
              "Rode o MESMO comando de novo mais tarde: ele refaz só essas.")
    com_empresa_csv = sum(1 for e in cid["linhas"].values() if "(" in (e.get("empresa") or ""))
    print(f"{com_empresa_csv} linha(s) da central mostram empresa + consórcio.")
    print(f"Central em: {os.path.abspath(a.saida)}")
    if a.resumo:
        salvar_json(a.resumo, {"geradas": feitas, "sem_mudanca": puladas, "erros": erros,
                               "sem_mapa": sem_mapa, "faltam": faltam, "mudaram": mudaram,
                               "empresas_csv": do_csv}, compacto=False)


if __name__ == "__main__":
    main()
