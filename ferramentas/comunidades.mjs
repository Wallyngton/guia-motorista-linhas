// Cópia de reserva dos LIMITES DAS COMUNIDADES (13/11/2026) para o aviso de
// comunidade do GPS do Guia do Motorista. O app baixa da Prefeitura; se a
// Prefeitura não responder (ou ainda não deu na primeira vez), ele usa esta
// cópia, publicada no branch "comunidades" deste repositório.
// Fonte: Prefeitura do Rio (IPP/SMH), licença CC BY 4.0 (pode copiar com o crédito).
// Uso: node comunidades.mjs <arquivo de saída>   (os MESMOS filtros do app)
import { writeFileSync } from 'node:fs';

const base = 'https://pgeo3.rio.rj.gov.br/arcgis/rest/services/Habitacao/Favelas_Urbanizacao/FeatureServer/0/query';
const saida = process.argv[2] || 'comunidades.json';
const features = [];
for (let offset = 0; offset < 5000; offset += 1000) {
  const u = `${base}?where=1%3D1&outFields=nome,complexo&outSR=4326&f=geojson` +
    `&geometryPrecision=5&maxAllowableOffset=0.00005&resultOffset=${offset}&resultRecordCount=1000`;
  const r = await fetch(u);
  if (!r.ok) { console.error(`HTTP ${r.status} no pedaço ${offset}`); process.exit(1); }
  const j = await r.json();
  const lista = Array.isArray(j?.features) ? j.features : [];
  features.push(...lista);
  const mais = j?.exceededTransferLimit === true || j?.properties?.exceededTransferLimit === true;
  if (!mais || lista.length === 0) break;
}
if (features.length < 500) { console.error(`só ${features.length} comunidades: não publico (a Prefeitura deve estar com problema)`); process.exit(1); }
writeFileSync(saida, JSON.stringify({
  type: 'FeatureCollection',
  fonte: 'Limites das comunidades: Prefeitura do Rio (IPP/SMH), licença CC BY 4.0',
  atualizado: new Date().toISOString(),
  features,
}));
console.log(`${features.length} comunidades`);
