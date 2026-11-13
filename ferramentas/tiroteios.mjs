// Robô dos TIROTEIOS RECENTES (v29; revisado em 13/11/2026): roda no GitHub
// Actions do repositório PÚBLICO (guia-motorista-linhas) e publica
// tiroteios.json no branch "tiroteios". O app lê de lá (TiroteiosRecentes) e
// avisa "tiroteio informado perto daqui" ao entrar numa comunidade.
// Fonte: Instituto Fogo Cruzado (dados gratuitos; precisa de cadastro na API).
// Credenciais SÓ nos segredos do GitHub: FOGOCRUZADO_EMAIL e FOGOCRUZADO_SENHA
// (nunca no código). Uso: node tiroteios.mjs <arquivo de saída>
//
// 13/11: o arquivo guarda as últimas 24 horas (antes 6): o GitHub não roda o
// robô a cada 10 minutos de verdade (rodava umas 4 vezes por dia), então 6 horas
// deixavam a lista quase sempre vazia. O app decide a idade que vale avisar.
// O registro diz quantas vieram e a mais nova, para conferir se a API responde.
import { writeFileSync } from 'node:fs';

const api = 'https://api-service.fogocruzado.org.br/api/v2';
const saida = process.argv[2] || 'tiroteios.json';
const janelaHoras = 24;
const email = process.env.FOGOCRUZADO_EMAIL, senha = process.env.FOGOCRUZADO_SENHA;
if (!email || !senha) { console.log('sem credenciais: nada a fazer'); process.exit(0); }

/** A data da API; sem fuso escrito, é a hora de Brasília (-03:00). */
function lerData(texto) {
  if (!texto) return null;
  let t = String(texto).trim().replace(' ', 'T');
  if (!/(Z|[+-]\d\d:?\d\d)$/.test(t)) t += '-03:00';
  const d = new Date(t);
  return Number.isNaN(d.getTime()) ? null : d;
}

const login = await fetch(`${api}/auth/login`, {
  method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ email, password: senha }),
}).then(r => r.json());
const token = login?.data?.accessToken;
if (!token) { console.error('login falhou'); process.exit(1); }
const cab = { Authorization: `Bearer ${token}` };

const estados = await fetch(`${api}/states`, { headers: cab }).then(r => r.json());
const rj = (estados?.data || []).find(e => /rio de janeiro/i.test(e.name));
if (!rj) { console.error('estado RJ não encontrado'); process.exit(1); }

const hoje = new Date(), antes = new Date(Date.now() - janelaHoras * 3600e3);
const dia = d => d.toISOString().slice(0, 10);
const ocorrencias = [];
let recebidas = 0, maisNova = null, maisVelha = null;
for (let page = 1; page <= 5; page++) {
  const u = `${api}/occurrences?idState=${rj.id}&initialdate=${dia(antes)}&finaldate=${dia(hoje)}&take=500&page=${page}&order=DESC`;
  const r = await fetch(u, { headers: cab });
  if (!r.ok) { console.error(`página ${page}: HTTP ${r.status}`); break; }
  const j = await r.json();
  for (const o of j?.data || []) {
    recebidas++;
    const quando = lerData(o.date);
    if (!quando) continue;
    if (!maisNova || quando > maisNova) maisNova = quando;
    if (!maisVelha || quando < maisVelha) maisVelha = quando;
    const idade = Date.now() - quando.getTime();
    if (idade > janelaHoras * 3600e3 || idade < -3600e3) continue;
    const lat = Number(o.latitude), lng = Number(o.longitude);
    if (!Number.isFinite(lat) || !Number.isFinite(lng)) continue;
    ocorrencias.push({ lat, lng, quando: quando.toISOString(), bairro: o?.neighborhood?.name || '' });
  }
  if (!j?.pageMeta?.hasNextPage) break;
}
writeFileSync(saida, JSON.stringify({ fonte: 'Instituto Fogo Cruzado', atualizado: new Date().toISOString(), horas: janelaHoras, ocorrencias }));
console.log(`${ocorrencias.length} ocorrências nas últimas ${janelaHoras} horas ` +
  `(a API mandou ${recebidas}; mais nova ${maisNova?.toISOString() ?? '-'}; mais velha ${maisVelha?.toISOString() ?? '-'})`);
