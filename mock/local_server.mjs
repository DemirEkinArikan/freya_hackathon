// Run a built mock on this machine with Node (no Cloudflare, records kept in memory):
//   node mock/local_server.mjs <build_dir> [port]        then  FREYA_BANK_HOST=http://127.0.0.1:<port> python3 build-the-builder/tools/score.py ...
import http from "node:http";
import path from "node:path";
import { pathToFileURL } from "node:url";

const [dir, port = "8787"] = process.argv.slice(2);
const mod = await import(pathToFileURL(path.resolve(dir, "worker.js")).href);

const data = new Map();
const storage = {
  async get(k) { return data.has(k) ? structuredClone(data.get(k)) : undefined; },
  async put(k, v) { data.set(k, structuredClone(v)); },
  async delete(k) { return data.delete(k); },
  async deleteAll() { data.clear(); },
  async list({ prefix = "" } = {}) { return new Map([...data].filter(([k]) => k.startsWith(prefix)).map(([k, v]) => [k, structuredClone(v)])); },
};
const env = { MOCK_ADMIN_KEY: process.env.MOCK_ADMIN_KEY };
const bank = new mod.Bank({ storage }, env);
env.BANK = { idFromName: () => "bank", get: () => ({ fetch: (request) => bank.fetch(request) }) };

http.createServer(async (req, res) => {
  const chunks = [];
  for await (const c of req) chunks.push(c);
  const hasBody = !["GET", "HEAD"].includes(req.method);
  const request = new Request("http://127.0.0.1:" + port + req.url, { method: req.method, headers: req.headers, body: hasBody ? Buffer.concat(chunks) : undefined });
  const response = await mod.default.fetch(request, env);
  res.writeHead(response.status, Object.fromEntries(response.headers));
  res.end(Buffer.from(await response.arrayBuffer()));
}).listen(Number(port), "127.0.0.1", () => console.log("mock listening on http://127.0.0.1:" + port));
