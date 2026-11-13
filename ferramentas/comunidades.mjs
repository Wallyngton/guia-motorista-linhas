// Cópia dos LIMITES DAS COMUNIDADES (13/11/2026) para o aviso de comunidade do
// GPS do Guia do Motorista, publicada no branch "comunidades" deste repositório.
// O app lê primeiro esta cópia (rápida, do GitHub) e, se não der, a Prefeitura.
// Fonte: Prefeitura do Rio (IPP/SMH), licença CC BY 4.0 (pode copiar com o crédito).
// Uso: node comunidades.mjs <arquivo de saída>
//
// 13/11: a Prefeitura respondeu HTTP 500 ao pedido que o app fazia (geojson com
// simplificação, 1000 de uma vez). Por isso o app ficava SEM comunidades e não
// avisava nada. Aqui tenta vários jeitos e registra qual deu certo.
import { writeFileSync } from 'node:fs';

const base = 'https://pgeo3.rio.rj.gov.br/arcgis/rest/services/Habitacao/Favelas_Urbanizacao/FeatureServer/0/query';
const saida = process.argv[2] || 'comunidades.json';

/** Esri JSON (rings) → GeoJSON Polygon/MultiPolygon. */
function paraGeojson(f) {
  const rings = f?.geometry?.rings;
  if (!Array.isArray(rings) || rings.length === 0) return null;
  const arred = r => r.map(([x, y]) => [Math.round(x * 1e5) / 1e5, Math.round(y * 1e5) / 1e5]);
  return {
    type: 'Feature',
    properties: { nome: f.attributes?.nome ?? '', complexo: f.attributes?.complexo ?? '' },
    geometry: rings.length === 1
      ? { type: 'Polygon', coordinates: [arred(rings[0])] }
      : { type: 'MultiPolygon', coordinates: rings.map(r => [arred(r)]) },
  };
}

const jeitos = [
  { nome: 'geojson simplificado, 1000', f: 'geojson', extra: '&geometryPrecision=5&maxAllowableOffset=0.00005', n: 1000 },
  { nome: 'geojson simplificado, 250', f: 'geojson', extra: '&geometryPrecision=5&maxAllowableOffset=0.00005', n: 250 },
  { nome: 'geojson, 250', f: 'geojson', extra: '&geometryPrecision=5', n: 250 },
  { nome: 'json simplificado, 250', f: 'json', extra: '&geometryPrecision=5&maxAllowableOffset=0.00005', n: 250 },
  { nome: 'json, 100', f: 'json', extra: '', n: 100 },
];

async function baixar(j) {
  const features = [];
  for (let offset = 0; offset < 6000; offset += j.n) {
    const u = `${base}?where=1%3D1&outFields=nome,complexo&outSR=4326&f=${j.f}${j.extra}` +
      `&orderByFields=objectid&resultOffset=${offset}&resultRecordCount=${j.n}`;
    const r = await fetch(u);
    if (!r.ok) throw new Error(`HTTP ${r.status} no pedaço ${offset}`);
    const d = await r.json();
    if (d?.error) throw new Error(`erro ${d.error.code}: ${d.error.message}`);
    const lista = Array.isArray(d?.features) ? d.features : [];
    for (const f of lista) {
      const g = j.f === 'json' ? paraGeojson(f) : f;
      if (g) features.push(g);
    }
    const mais = d?.exceededTransferLimit === true || d?.properties?.exceededTransferLimit === true;
    if (!mais || lista.length === 0) break;
  }
  return features;
}

let features = [], usado = '';
for (const j of jeitos) {
  try {
    features = await baixar(j);
    console.log(`${j.nome}: ${features.length} comunidades`);
    if (features.length >= 500) { usado = j.nome; break; }
  } catch (e) {
    console.log(`${j.nome}: falhou (${e.message})`);
  }
}
if (!usado) { console.error('nenhum jeito deu: não publico (a Prefeitura deve estar com problema)'); process.exit(1); }
writeFileSync(saida, JSON.stringify({
  type: 'FeatureCollection',
  fonte: 'Limites das comunidades: Prefeitura do Rio (IPP/SMH), licença CC BY 4.0',
  atualizado: new Date().toISOString(),
  jeito: usado,
  features,
}));
console.log(`publicando ${features.length} comunidades (${usado})`);
