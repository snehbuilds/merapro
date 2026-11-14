// A stand-in for the WhatsApp Cloud API (graph.facebook.com) for the Phase 2 browser
// tests (P2-M10.2). Mera's WhatsApp channel points at it through WHATSAPP_GRAPH_URL; it
// records every message Mera sends and answers like the real API. Test-only: never
// configure it outside the verify stack.
//
//   POST /v23.0/{phone-number-id}/messages   record, answer { messages: [{ id }] }
//   GET  /_sent?to=<wa id>                   what was sent to that chat, oldest first
//   POST /_reset                             forget everything
import { createServer } from "node:http";

const port = Number(process.env.FAKE_GRAPH_PORT ?? 8795);
let sent = [];
let next = 1;

function json(res, status, body) {
  res.writeHead(status, { "content-type": "application/json" });
  res.end(JSON.stringify(body));
}

createServer((req, res) => {
  const url = new URL(req.url ?? "/", `http://localhost:${port}`);
  if (req.method === "GET" && url.pathname === "/_sent") {
    const to = url.searchParams.get("to");
    return json(res, 200, { data: sent.filter((m) => !to || m.to === to) });
  }
  if (req.method === "POST" && url.pathname === "/_reset") {
    sent = [];
    return json(res, 200, { ok: true });
  }
  if (req.method === "POST" && /^\/v\d+\.\d+\/[^/]+\/messages$/.test(url.pathname)) {
    let raw = "";
    req.on("data", (c) => (raw += c));
    req.on("end", () => {
      if (req.headers.authorization !== `Bearer ${process.env.FAKE_GRAPH_TOKEN ?? "e2e-wa-token"}`) {
        return json(res, 401, { error: { code: 190, message: "Invalid OAuth access token" } });
      }
      let body;
      try {
        body = JSON.parse(raw);
      } catch {
        return json(res, 400, { error: { code: 100, message: "Invalid JSON" } });
      }
      const id = `wamid.fake.${next++}`;
      sent.push({ id, to: body.to, type: body.type, body, at: new Date().toISOString() });
      return json(res, 200, { messaging_product: "whatsapp", contacts: [{ wa_id: body.to }], messages: [{ id }] });
    });
    return;
  }
  json(res, 404, { error: { message: "not found" } });
}).listen(port, "127.0.0.1", () => {
  console.log(`fake WhatsApp Graph API on http://127.0.0.1:${port}`);
});
