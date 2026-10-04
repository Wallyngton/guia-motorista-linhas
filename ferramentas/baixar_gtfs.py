#!/usr/bin/env python3
"""
Baixa o GTFS mais recente dos ônibus do Rio (usado pela atualização
automática da central, no GitHub Actions — mas roda em qualquer computador).

    python baixar_gtfs.py --saida gtfs_rio.zip

Tenta, nesta ordem (o primeiro que der um GTFS válido vale):
  1. o endereço passado em --url (ou na variável GTFS_URL), se houver;
  2. a cópia mais recente do Mobility Database (feed mdb-1791):
     https://files.mobilitydatabase.org/mdb-1791/latest.zip
  3. o endereço "urls.latest"/"urls.direct_download" desse feed na lista
     https://files.mobilitydatabase.org/feeds_v2.csv
  4. o arquivo publicado pela Prefeitura (data.rio / ArcGIS).
Só usa a biblioteca padrão do Python.
"""
import argparse
import csv
import hashlib
import io
import os
import shutil
import sys
import tempfile
import time
import urllib.request
import zipfile

FEED = "mdb-1791"
MDB = "https://files.mobilitydatabase.org"
PRODUTOR = "https://www.arcgis.com/sharing/rest/content/items/8ffe62ad3b2f42e49814bf941654ea6c/data"
USER_AGENT = "GuiaMotoristaOnibus/1.0 (atualizacao da central)"
OBRIGATORIOS = ("routes.txt", "trips.txt", "stop_times.txt", "stops.txt", "shapes.txt")


def _abrir(url, timeout=600):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    return urllib.request.urlopen(req, timeout=timeout)


def baixar_para(url, destino, tentativas=3):
    """Baixa `url` para o arquivo `destino` (em pedaços, sem encher a memória)."""
    for n in range(tentativas):
        try:
            with _abrir(url) as r, open(destino, "wb") as f:
                shutil.copyfileobj(r, f, 1024 * 1024)
            return
        except Exception as e:  # noqa: BLE001
            if n == tentativas - 1:
                raise
            print(f"  (falhou: {e}; tentando de novo...)")
            time.sleep(5 * (n + 1))


def gtfs_valido(caminho):
    """É um .zip com os arquivos de que o gerador precisa?"""
    try:
        with zipfile.ZipFile(caminho) as z:
            nomes = {os.path.basename(n) for n in z.namelist()}
        faltam = [n for n in OBRIGATORIOS if n not in nomes]
        return (not faltam), ("faltam " + ", ".join(faltam)) if faltam else "ok"
    except zipfile.BadZipFile:
        return False, "não é um arquivo .zip"


def enderecos_do_catalogo(feed):
    """Procura o feed na lista pública do Mobility Database."""
    try:
        with _abrir(f"{MDB}/feeds_v2.csv", timeout=120) as r:
            texto = r.read().decode("utf-8-sig", errors="replace")
    except Exception as e:  # noqa: BLE001
        print(f"  lista do Mobility Database indisponível ({e})")
        return []
    for linha in csv.DictReader(io.StringIO(texto)):
        if (linha.get("id") or "").strip() == feed:
            return [u for u in (linha.get("urls.latest"), linha.get("urls.direct_download")) if u]
    return []


def candidatos(url, feed, produtor):
    vistos = []
    if url:
        vistos.append(url)
    vistos.append(f"{MDB}/{feed}/latest.zip")
    vistos.append(lambda: enderecos_do_catalogo(feed))
    vistos.append(produtor)
    return vistos


def main():
    ap = argparse.ArgumentParser(description="Baixa o GTFS mais recente do Rio.")
    ap.add_argument("--saida", default="gtfs_rio.zip")
    ap.add_argument("--url", default=os.environ.get("GTFS_URL", "").strip(),
                    help="endereço do GTFS (opcional; vazio = automático)")
    ap.add_argument("--feed", default=FEED, help="id do feed no Mobility Database")
    ap.add_argument("--produtor", default=PRODUTOR, help="endereço da Prefeitura (último recurso)")
    a = ap.parse_args()

    pasta = os.path.dirname(os.path.abspath(a.saida))
    os.makedirs(pasta, exist_ok=True)
    tentados = set()
    for c in candidatos(a.url, a.feed, a.produtor):
        lista = c() if callable(c) else [c]
        for url in lista:
            if not url or url in tentados:
                continue
            tentados.add(url)
            print(f"Baixando {url} ...")
            fd, tmp = tempfile.mkstemp(suffix=".zip", dir=pasta)
            os.close(fd)
            try:
                baixar_para(url, tmp)
                ok, motivo = gtfs_valido(tmp)
                if not ok:
                    print(f"  arquivo recusado: {motivo}")
                    continue
                os.replace(tmp, a.saida)
                h = hashlib.sha256()
                with open(a.saida, "rb") as f:
                    for bloco in iter(lambda: f.read(1024 * 1024), b""):
                        h.update(bloco)
                tam = os.path.getsize(a.saida) / 1e6
                print(f"GTFS salvo em {os.path.abspath(a.saida)} ({tam:.1f} MB, sha256 {h.hexdigest()[:16]})")
                print(f"Origem: {url}")
                return
            except Exception as e:  # noqa: BLE001
                print(f"  não deu: {e}")
            finally:
                if os.path.exists(tmp):
                    os.remove(tmp)
    sys.exit("Não consegui baixar o GTFS de nenhum endereço. "
             "Rode de novo mais tarde ou informe o endereço em 'gtfs_url'.")


if __name__ == "__main__":
    main()
