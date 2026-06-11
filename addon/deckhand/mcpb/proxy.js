#!/usr/bin/env node
// Deckhand MCPB proxy: bridges Claude Desktop's stdio MCP transport to the
// Deckhand companion's local Streamable HTTP endpoint. Zero dependencies so
// it runs on the Node runtime bundled with Claude Desktop.
"use strict";

const ENDPOINT = process.env.DECKHAND_MCP_URL || "http://127.0.0.1:28765/mcp";
const TOKEN = process.env.DECKHAND_MCP_TOKEN || "";

let sessionId = null;
let buffer = "";

process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => {
  buffer += chunk;
  let index;
  while ((index = buffer.indexOf("\n")) >= 0) {
    const line = buffer.slice(0, index).trim();
    buffer = buffer.slice(index + 1);
    if (line) forward(line).catch((error) => fail(line, error));
  }
});
process.stdin.on("end", () => process.exit(0));

async function forward(line) {
  const headers = {
    "content-type": "application/json",
    accept: "application/json, text/event-stream",
  };
  if (sessionId) headers["mcp-session-id"] = sessionId;
  if (TOKEN) headers.authorization = `Bearer ${TOKEN}`;

  const response = await fetch(ENDPOINT, { method: "POST", headers, body: line });
  const newSession = response.headers.get("mcp-session-id");
  if (newSession) sessionId = newSession;

  if (response.status === 202 || response.status === 204) return;
  if (!response.ok) throw new Error(`Deckhand answered HTTP ${response.status}. Is Anki running?`);

  const contentType = (response.headers.get("content-type") || "").toLowerCase();
  if (contentType.includes("text/event-stream")) {
    await pumpEventStream(response);
    return;
  }
  const text = await response.text();
  for (const piece of text.split("\n")) {
    if (piece.trim()) process.stdout.write(piece.trim() + "\n");
  }
}

async function pumpEventStream(response) {
  const decoder = new TextDecoder();
  let pending = "";
  for await (const chunk of response.body) {
    pending += decoder.decode(chunk, { stream: true });
    let separator;
    while ((separator = pending.indexOf("\n\n")) >= 0) {
      const rawEvent = pending.slice(0, separator);
      pending = pending.slice(separator + 2);
      const data = rawEvent
        .split("\n")
        .filter((eventLine) => eventLine.startsWith("data:"))
        .map((eventLine) => eventLine.slice(5).trim())
        .join("\n");
      if (data) process.stdout.write(data + "\n");
    }
  }
}

function fail(line, error) {
  let id = null;
  try {
    id = JSON.parse(line).id ?? null;
  } catch (_parseError) {
    // Unparseable input: nothing to correlate an error response with.
  }
  if (id === null) {
    process.stderr.write(`deckhand proxy: ${error && error.message ? error.message : error}\n`);
    return;
  }
  process.stdout.write(
    JSON.stringify({
      jsonrpc: "2.0",
      id,
      error: { code: -32000, message: `deckhand proxy: ${error && error.message ? error.message : error}` },
    }) + "\n"
  );
}
