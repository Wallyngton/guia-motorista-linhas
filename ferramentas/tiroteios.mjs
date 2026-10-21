// Robô dos TIROTEIOS RECENTES (v29): roda no GitHub Actions do repositório
// PÚBLICO (guia-motorista-linhas), a cada 10 minutos, e publica tiroteios.json
// no site do app. O app lê de lá (TiroteiosRecentes) e avisa "tiroteio
// informado perto daqui" ao entrar numa comunidade.
// Fonte: Instituto Fogo Cruzado (dados gratuitos; precisa de cadastro na API).
// Credenciais SÓ nos segredos do GitHub: FOGOCRUZADO_EMAIL e FOGOCRUZADO_SENHA
// (nunca no código). Uso: node tiroteios.mjs <arquivo de saída>
import { writeFileSync } from 'node:fs';

const api = 'https://api-service.fogocruzado.org.br/api/v2';
const saida = process.argv[2] || 'tiroteios.json';
const email = process.env.FOGOCRUZADO_EMAIL, senha = process.env.FOGOCRUZADO_SENHA;
if (!email || !senha) { console.log('sem credenciais: nada a fazer'); process.exit(0); }

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

const hoje = new Date(), ontem = new Date(Date.now() - 24 * 3600e3);
const dia = d => d.toISOString().slice(0, 10);
const ocorrencias = [];
for (let page = 1; page <= 5; page++) {
  const u = `${api}/occurrences?idState=${rj.id}&initialdate=${dia(ontem)}&finaldate=${dia(hoje)}&take=500&page=${page}&order=DESC`;
  const j = await fetch(u, { headers: cab }).then(r => r.json());
  for (const o of j?.data || []) {
    const quando = new Date(o.date);
    if (Date.now() - quando.getTime() > 6 * 3600e3) continue; // só as últimas 6 horas
    ocorrencias.push({ lat: Number(o.latitude), lng: Number(o.longitude), quando: quando.toISOString(), bairro: o?.neighborhood?.name || '' });
  }
  if (!j?.pageMeta?.hasNextPage) break;
}
writeFileSync(saida, JSON.stringify({ fonte: 'Instituto Fogo Cruzado', atualizado: new Date().toISOString(), ocorrencias }));
console.log(`${ocorrencias.length} ocorrências nas últimas 6 horas`);
