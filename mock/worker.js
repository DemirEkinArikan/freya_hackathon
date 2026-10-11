// Bank mock on Cloudflare Workers. Serves every operation of the service contract described by config.js
// (written by build_mock.py), with records kept per customer and per session in one Durable Object.
//
// Tool calls:  <method> <base_path><path>  as the contract declares them.
// Records:     POST /v1/practice/sessions {"case": id} -> fresh customer id, GET /v1/practice/ledger/<id>   (the kit's score.py)
//              POST /_mock/sessions {"customer": id}, GET /_mock/ledger/<id>, GET /_mock/ledgers, GET /_mock/health,
//              POST /_mock/reset {"customer_id"?} (only with header x-admin-key = secret MOCK_ADMIN_KEY)
import CONFIG from "./config.js";

const FAILED = "service unavailable, nothing was recorded";
const KEEP_SESSIONS = 10;

const reply = (status, body) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json; charset=utf-8" } });

export default {
  async fetch(request, env) {
    try {
      return await env.BANK.get(env.BANK.idFromName("bank")).fetch(request);
    } catch (e) {
      return reply(500, { ok: false, error: FAILED });
    }
  },
};

export class Bank {
  constructor(ctx, env) {
    this.storage = ctx.storage;
    this.env = env || {};
  }
  async fetch(request) {
    try {
      return await route(request, this.storage, this.env);
    } catch (e) {
      return reply(500, { ok: false, error: FAILED });
    }
  }
}

async function route(request, storage, env) {
  const url = new URL(request.url);
  const path = url.pathname.replace(/\/+$/, "") || "/";
  const method = request.method.toUpperCase();
  let m;
  if (method === "GET" && (path === "/" || path === "/_mock/health")) return reply(200, health());
  if (method === "POST" && (path === "/v1/practice/sessions" || path === "/_mock/sessions")) return newAlias(storage, await body(request));
  if (method === "GET" && (m = /^\/(?:v1\/practice|_mock)\/ledger\/([^/]+)$/.exec(path))) return ledger(storage, decodeURIComponent(m[1]));
  if (method === "GET" && path === "/_mock/ledgers") return ledgers(storage);
  if (method === "POST" && path === "/_mock/reset") {
    if (!env.MOCK_ADMIN_KEY || request.headers.get("x-admin-key") !== env.MOCK_ADMIN_KEY) return reply(403, { ok: false, error: "reset needs x-admin-key" });
    const b = (await body(request)) || {};
    if (b.customer_id) await storage.delete("c:" + b.customer_id);
    else await storage.deleteAll();
    return reply(200, { ok: true });
  }
  const found = findOperation(method, path);
  if (found === "method") return reply(405, { ok: false, error: "method not allowed" });
  if (!found) return reply(404, { ok: false, error: "unknown operation" });
  return operation(request, storage, found.op, found.params, url);
}

function health() {
  return {
    ok: true, mock: CONFIG.title, built_at: CONFIG.built_at, base_path: CONFIG.base_path, customers: Object.keys(CONFIG.customers).length,
    operations: CONFIG.operations.map((o) => ({ name: o.name, method: o.method, path: CONFIG.base_path + o.path, kind: o.kind })),
  };
}

async function body(request) {
  const text = await request.text();
  if (!text.trim()) return {};
  try {
    const value = JSON.parse(text);
    return value && typeof value === "object" && !Array.isArray(value) ? value : null;
  } catch (e) {
    return null;
  }
}

function matchPath(template, path) {
  const t = template.split("/").filter(Boolean), p = path.split("/").filter(Boolean);
  if (t.length !== p.length) return null;
  const params = {};
  for (let i = 0; i < t.length; i++) {
    const m = /^\{(.+)\}$/.exec(t[i]);
    if (m) params[m[1]] = decodeURIComponent(p[i]);
    else if (t[i] !== p[i]) return null;
  }
  return params;
}

function findOperation(method, path) {
  let pathOnly = false;
  for (const op of CONFIG.operations) {
    for (const full of [CONFIG.base_path + op.path, op.path]) {
      const params = matchPath(full, path);
      if (!params) continue;
      if (op.method === method) return { op, params };
      pathOnly = true;
    }
  }
  return pathOnly ? "method" : null;
}

function hex(n) {
  return [...crypto.getRandomValues(new Uint8Array(n))].map((b) => b.toString(16).padStart(2, "0")).join("").slice(0, n);
}

async function newAlias(storage, b) {
  const id = b && String(b.case ?? b.customer ?? b[CONFIG.customer_key] ?? "");
  const customer = CONFIG.customers[id];
  if (!customer) return reply(404, { ok: false, error: "unknown case" });
  const alias = "c_" + hex(8) + "-" + hex(3);
  await storage.put("a:" + alias, { customer: id, created: Date.now() });
  return reply(200, { ok: true, case: id, variables: { ...customer.variables, [CONFIG.customer_key]: alias } });
}

function newSession(state, customer, now) {
  const s = {
    id: "s" + (state.count = (state.count || 0) + 1), started_at: now, last_at: now, closed: false,
    verified: {}, wrong: 0, hinted: false, failures: {}, seq: {}, records: {}, calls: [],
  };
  for (const op of CONFIG.operations) {
    if (op.kind === "write") s.records[op.ledger_key] = [];
    if (op.kind === "close") for (const f of op.fields) s.records[f] = null;
  }
  state.sessions.push(s);
  state.sessions = state.sessions.slice(-KEEP_SESSIONS);
  return s;
}

function view(state, s, customer) {
  return {
    ok: true, customer_id: state.customer_id, case: state.case, now: customer.variables.now ?? null, session: s.id, sessions: state.count,
    verified: s.verified, wrong: s.wrong, hinted: s.hinted, ...s.records, closed: s.closed, failures: s.failures,
    calls: s.calls, started_at: s.started_at, last_at: s.last_at,
  };
}

async function ledger(storage, id) {
  const state = await storage.get("c:" + id);
  const alias = state ? null : await storage.get("a:" + id);
  const template = state ? state.case : alias ? alias.customer : id;
  const customer = CONFIG.customers[template];
  if (!customer) return reply(404, { ok: false, error: "unknown customer" });
  const st = state || { customer_id: id, case: template, sessions: [] };
  return reply(200, view(st, st.sessions[st.sessions.length - 1] || newSession(st, customer, Date.now()), customer));
}

async function ledgers(storage) {
  const out = {};
  for (const [key, st] of await storage.list({ prefix: "c:" })) {
    const s = st.sessions[st.sessions.length - 1];
    out[key.slice(2)] = { case: st.case, sessions: st.count, calls: s.calls.map((c) => c.tool + ":" + c.status + (c.result.ok === false ? ":" + c.result.error : "")), closed: s.closed, last_at: s.last_at };
  }
  return reply(200, { ok: true, customers: out, unknown_customer_calls: (await storage.get("orphans")) || [] });
}

// The answer in the contract's own fields: its success flag (boolean or "true"/"false"), its error field and declared codes.
function shape(op, result) {
  const o = op.shape;
  if (!o) return result;
  const { ok, error, ...rest } = result;
  const r = o.ok ? { [o.ok]: ok === false ? o.no : o.yes } : {};
  if (ok === false) r[o.error || "error"] = o.codes ? code(o.codes, error) : error;
  for (const [k, v] of Object.entries(rest)) r[k] = o.bools[k] && typeof v === "boolean" ? o.bools[k][v ? 0 : 1] : v;
  return r;
}

function code(codes, text) {
  const want = text === FAILED ? /unavail|temporar|timeout|service|later|busy/ : /identity|verif/.test(text) ? /verif|denied|identity|auth|forbid/ : /invalid|request|valid|bad|format/;
  return codes.find((c) => want.test(String(c).toLowerCase())) ?? codes[0];
}

async function operation(request, storage, op, params, url) {
  const send = (status, result) => reply(status, shape(op, result));
  const raw = op.method === "GET" || op.method === "DELETE" ? {} : await body(request);
  if (raw === null) return send(op.error_status, { ok: false, error: "body must be a JSON object" });
  const args = { ...raw };
  for (const [k, v] of url.searchParams) if (!(k in args)) args[k] = v;
  Object.assign(args, params);
  const key = CONFIG.customer_key;
  const cid = args[key] == null ? "" : String(args[key]).trim();
  if (!cid) return send(op.error_status, { ok: false, error: key + " is required" });

  const alias = await storage.get("a:" + cid);
  const template = alias ? alias.customer : cid;
  const customer = CONFIG.customers[template];
  if (!customer) {
    const orphans = ((await storage.get("orphans")) || []).slice(-99);
    orphans.push({ at: Date.now(), tool: op.name, args });
    await storage.put("orphans", orphans);
    return send(op.error_status, { ok: false, error: "unknown " + key });
  }
  const now = Date.now();
  const state = (await storage.get("c:" + cid)) || { customer_id: cid, case: template, sessions: [] };
  let s = state.sessions[state.sessions.length - 1];
  // A practice session id is one call. A customer's own id serves call after call: a close, or a long silence, starts the next one.
  if (!s || (!alias && (s.closed || now - s.last_at > CONFIG.session_idle_seconds * 1000))) s = newSession(state, customer, now);

  let status = 200, result;
  const fault = (customer.faults || []).find((f) => f.tool === op.name);
  if (s.closed) result = { ok: false, error: "the call is already closed" };
  else if (fault && (s.failures[op.name] || 0) < (fault.fail_first_n || 0)) {
    s.failures[op.name] = (s.failures[op.name] || 0) + 1;
    status = 500;
    result = { ok: false, error: FAILED };
  } else {
    const checked = validate(op, args);
    result = checked.error ? { ok: false, error: checked.error } : KINDS[op.kind](op, checked.args, s, customer, now);
  }
  if (result.ok === false && status === 200) status = op.error_status;
  s.failures[op.name] = s.failures[op.name] || 0;
  const { [key]: _drop, ...logged } = args;
  s.calls.push({ n: s.calls.length + 1, tool: op.name, args: logged, status, result, at: now });
  s.last_at = now;
  await storage.put("c:" + cid, state);
  return send(status, result);
}

function validate(op, args) {
  const out = { ...args };
  const schema = op.body || {};
  for (const f of schema.required || []) if (out[f] == null || out[f] === "") return { error: f + " is required" };
  for (const [f, p] of Object.entries(schema.properties || {})) {
    if (out[f] == null || out[f] === "") continue;
    const v = coerce(p, out[f]);
    if (v.error) return { error: f + " " + v.error };
    out[f] = v.value;
  }
  return { args: out };
}

const isDate = (s) => {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s);
  if (!m) return false;
  const d = new Date(Date.UTC(+m[1], +m[2] - 1, +m[3]));
  return d.getUTCFullYear() === +m[1] && d.getUTCMonth() === +m[2] - 1 && d.getUTCDate() === +m[3];
};

function coerce(p, v) {
  if (p.enum) {
    const hit = p.enum.find((e) => String(e).toLowerCase() === String(v).trim().toLowerCase());
    return hit === undefined ? { error: "must be one of " + p.enum.join(", ") } : { value: hit };
  }
  const t = p.type;
  if (t === "number" || t === "integer") {
    const n = typeof v === "number" ? v : String(v).trim() === "" ? NaN : Number(String(v).trim());
    if (!Number.isFinite(n) || (t === "integer" && !Number.isInteger(n))) return { error: "must be a " + t };
    if (p.minimum != null && n < p.minimum) return { error: "must be at least " + p.minimum };
    if (p.maximum != null && n > p.maximum) return { error: "must be at most " + p.maximum };
    return { value: n };
  }
  if (t === "boolean") {
    if (typeof v === "boolean") return { value: v };
    const s = String(v).trim().toLowerCase();
    return s === "true" || s === "false" ? { value: s === "true" } : { error: "must be true or false" };
  }
  if (t === "array") return Array.isArray(v) ? { value: v } : { error: "must be a list" };
  if (t === "object") return typeof v === "object" && !Array.isArray(v) ? { value: v } : { error: "must be an object" };
  if (typeof v === "object") return { error: "must be a string" };
  let s = String(v).trim();
  if (p.format === "date" && !isDate(s)) return { error: "must be a date as YYYY-MM-DD" };
  if (p["x-time"]) {
    const m = /^(\d{1,2})[:.](\d{2})(?::\d{2})?$/.exec(s);
    if (!m || +m[1] > 23 || +m[2] > 59) return { error: "must be a time as HH:MM" };
    s = m[1].padStart(2, "0") + ":" + m[2];
  }
  if (p.pattern && !new RegExp(p.pattern).test(s)) return { error: "must match " + p.pattern };
  if (p.minLength != null && s.length < p.minLength) return { error: "is too short" };
  if (p.maxLength != null && s.length > p.maxLength) return { error: "is too long" };
  return { value: s };
}

const identityDone = (s) => !CONFIG.identity || CONFIG.identity.required.every((f) => s.verified[f]);
const fold = (v) => String(v).toLocaleLowerCase("tr").normalize("NFKD").replace(/[\u0300-\u036f]/g, "").replace(/ı/g, "i").replace(/[^a-z0-9]/g, "");

function readAnswer(format, v) {
  const s = String(v).trim();
  if (format === "date") return isDate(s) ? s : null;
  if (format === "digits") {
    const d = s.replace(/[\s\-.\/()]/g, "");
    return /^\d+$/.test(d) ? d : null;
  }
  return fold(s) || null;
}

function sameAnswer(format, truth, answer) {
  if (format === "digits") return String(truth) === answer || Number(truth) === Number(answer);
  if (format === "text") return fold(truth) === answer;
  return String(truth) === answer;
}

const KINDS = {
  identity(op, a, s, c) {
    const I = op.identity, G = CONFIG.identity, O = I.out;
    const pack = (ok, verified, extra) => {
      const r = { ok };
      if (O.verified) r[O.verified] = verified;
      if (O.remaining) r[O.remaining] = Math.max(0, G.max_wrong - s.wrong);
      if (O.all_verified) r[O.all_verified] = identityDone(s);
      return Object.assign(r, extra || {});
    };
    if (s.wrong >= G.max_wrong) return pack(false, false, { error: "identity check failed: no wrong answers left" });
    const pairs = I.field_param ? [[a[I.field_param], a[I.answer_param]]] : I.fields.filter((f) => a[f] != null && a[f] !== "").map((f) => [f, a[f]]);
    if (!pairs.length) return pack(false, false, { error: "no answer given" });
    const read = [];
    for (const [field, answer] of pairs) {
      const format = G.formats[field] || "text";
      if (c.identity[field] == null || c.identity[field] === "") return pack(false, false, { error: "no " + field + " on file for this customer" });
      const value = answer == null ? null : readAnswer(format, answer);
      if (value === null) return pack(false, false, { error: format === "date" ? "answer is not a date. Send it as YYYY-MM-DD." : "answer is not readable as " + (format === "digits" ? "digits" : "text") });
      read.push([field, format, value]);
    }
    let all = true;
    for (const [field, format, value] of read) {
      const truth = String(c.identity[field]);
      if (sameAnswer(format, truth, value)) {
        s.verified[field] = true;
        continue;
      }
      all = false;
      if (format === "date" && O.hint && I.hint_value && !s.hinted && value.slice(0, 4) + "-" + value.slice(8, 10) + "-" + value.slice(5, 7) === truth) {
        s.hinted = true;
        return pack(true, false, { [O.hint]: I.hint_value });
      }
      s.wrong += 1;
    }
    return pack(true, all);
  },

  read(op, a, s, c) {
    if (op.requires_identity && !identityDone(s)) return { ok: false, error: "identity not verified" };
    let products = c.products;
    if (op.product_ref && a[op.product_ref] != null) {
      products = products.filter((p) => String(p[CONFIG.product_id]) === String(a[op.product_ref]));
      if (!products.length) return { ok: false, error: "unknown " + op.product_ref };
    }
    const r = { ok: true };
    if (op.list) r[op.list] = products.map((p) => Object.fromEntries(op.item_fields.filter((f) => p[f] != null).map((f) => [f, p[f]])));
    const one = products.length === 1 ? products[0] : {};
    const pick = (k) => c.variables[k] ?? c.identity[k] ?? one[k];
    for (const k of op.scalars) if (pick(k) !== undefined) r[k] = pick(k);
    for (const [k, fields] of Object.entries(op.objects || {})) {
      const o = Object.fromEntries(fields.map((f) => [f, pick(f)]).filter(([, v]) => v !== undefined));
      if (Object.keys(o).length) r[k] = o;
    }
    return r;
  },

  write(op, a, s, c) {
    if (op.requires_identity && !identityDone(s)) return { ok: false, error: "identity not verified" };
    const list = s.records[op.ledger_key];
    if (op.product_ref && a[op.product_ref] != null) {
      if (!c.products.some((p) => String(p[CONFIG.product_id]) === String(a[op.product_ref]))) return { ok: false, error: "unknown " + op.product_ref };
      if (op.unique && list.some((x) => String(x[op.product_ref]) === String(a[op.product_ref]))) return { ok: false, error: "already recorded for this " + op.product_ref };
    }
    const { [CONFIG.customer_key]: _drop, ...record } = a;
    const r = { ok: true };
    for (const f of op.ids) {
      s.seq[f] = (s.seq[f] || 0) + 1;
      record[f] = r[f] = op.id_prefix[f] + s.seq[f];
    }
    list.push(record);
    return r;
  },

  close(op, a, s, c, now) {
    for (const rule of op.require) {
      if ((a[rule.field] == null || a[rule.field] === "") && !rule.unless.includes(a[rule.unless_field])) {
        return { ok: false, error: rule.field + " is required for " + rule.unless_field + " " + a[rule.unless_field] };
      }
    }
    for (const f of op.fields) s.records[f] = a[f] ?? null;
    s.closed = true;
    s.closed_at = now;
    return { ok: true };
  },
};
