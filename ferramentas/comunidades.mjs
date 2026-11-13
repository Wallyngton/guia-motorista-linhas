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

// Os serviços da Prefeitura com os limites (o 1º é o que o app usava).
const servicos = [
  'https://pgeo3.rio.rj.gov.br/arcgis/rest/services/Habitacao/Favelas_Urbanizacao/FeatureServer/0/query',
  'https://pgeo3.rio.rj.gov.br/arcgis/rest/services/SABREN/Limites_de_Favelas/FeatureServer/13/query', // 2022
  'https://pgeo3.rio.rj.gov.br/arcgis/rest/services/SABREN/Limites_de_Favelas/FeatureServer/12/query', // 2019
  'https://pgeo3.rio.rj.gov.br/arcgis/rest/services/SMH/Limites_de_Favelas/MapServer/12/query', // 2019
];
// Cópias que o portal DATA.RIO (ArcGIS Hub) guarda prontas para baixar.
const copiasDoPortal = [
  'https://www.data.rio/api/download/v1/items/211856b90ba64e3fb6fc0941f5dbda47/geojson?layers=13',
  'https://www.data.rio/api/download/v1/items/cc3e958a3e47439d828e582501a97eba/geojson?layers=12',
];

/** O nome da comunidade, qualquer que seja o nome do campo. */
function nomeDe(p) {
  if (!p) return '';
  for (const k of Object.keys(p)) if (/^(nome|nm_favela|nome_favela|favela)$/i.test(k) && p[k]) return String(p[k]).trim();
  return '';
}
function complexoDe(p) {
  if (!p) return '';
  for (const k of Object.keys(p)) if (/^(complexo|nome_complexo|nm_complexo)$/i.test(k) && p[k]) return String(p[k]).trim();
  return '';
}
/** Deixa só o que o app usa (nome, complexo, geometria com 5 casas). */
function limpar(f) {
  const nome = nomeDe(f.properties);
  if (!nome || !f.geometry) return null;
  const arred = c => Array.isArray(c[0]) ? c.map(arred) : [Math.round(c[0] * 1e5) / 1e5, Math.round(c[1] * 1e5) / 1e5];
  return { type: 'Feature', properties: { nome, complexo: complexoDe(f.properties) },
    geometry: { type: f.geometry.type, coordinates: arred(f.geometry.coordinates) } };
}
const saida = process.argv[2] || 'comunidades.json';

/** Esri JSON (rings) → GeoJSON Polygon/MultiPolygon. */
function paraGeojson(f) {
  const rings = f?.geometry?.rings;
  if (!Array.isArray(rings) || rings.length === 0) return null;
  const arred = r => r.map(([x, y]) => [Math.round(x * 1e5) / 1e5, Math.round(y * 1e5) / 1e5]);
  return {
    type: 'Feature',
    properties: f.attributes ?? {},
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

async function baixar(base, j) {
  const features = [];
  for (let offset = 0; offset < 6000; offset += j.n) {
    const u = `${base}?where=1%3D1&outFields=*&outSR=4326&f=${j.f}${j.extra}` +
      `&orderByFields=objectid&resultOffset=${offset}&resultRecordCount=${j.n}`;
    const r = await fetch(u);
    if (!r.ok) throw new Error(`HTTP ${r.status} no pedaço ${offset}`);
    const d = await r.json();
    if (d?.error) throw new Error(`erro ${d.error.code}: ${d.error.message}`);
    const lista = Array.isArray(d?.features) ? d.features : [];
    for (const f of lista) {
      const g = limpar(j.f === 'json' ? paraGeojson(f) ?? {} : f);
      if (g) features.push(g);
    }
    const mais = d?.exceededTransferLimit === true || d?.properties?.exceededTransferLimit === true;
    if (!mais || lista.length === 0) break;
  }
  return features;
}

let features = [], usado = '';
fora: for (const base of servicos) {
  for (const j of jeitos) {
    try {
      features = await baixar(base, j);
      console.log(`${base} | ${j.nome}: ${features.length} comunidades`);
      if (features.length >= 500) { usado = `${base} (${j.nome})`; break fora; }
    } catch (e) {
      console.log(`${base} | ${j.nome}: falhou (${e.message})`);
    }
  }
}
if (!usado) {
  for (const u of copiasDoPortal) {
    try {
      const r = await fetch(u, { redirect: 'follow' });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const d = await r.json();
      features = (Array.isArray(d?.features) ? d.features : []).map(limpar).filter(Boolean);
      console.log(`${u}: ${features.length} comunidades`);
      if (features.length >= 500) { usado = u; break; }
    } catch (e) {
      console.log(`${u}: falhou (${e.message})`);
    }
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
