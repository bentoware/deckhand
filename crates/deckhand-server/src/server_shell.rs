use anyhow::{anyhow, Context, Result};
use base64::Engine as _;
use bytes::Bytes;
use http::{HeaderMap, HeaderName, HeaderValue, Request, StatusCode};
use http_body_util::{BodyExt, Full};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha1::{Digest, Sha1};
use std::cmp::Ordering as CmpOrdering;
use std::collections::HashMap;
use std::env;
use std::net::SocketAddr;
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex as StdMutex, OnceLock};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::{TcpListener, TcpStream};
use tokio::sync::{mpsc, oneshot, Mutex};
use tokio::time::{sleep, timeout, Duration};
use tower_service::Service;
use tracing::info;

use crate::anki_mcp_server;
const ANKI_SDK_ANKI_PATH_PLACEHOLDER: &str = "{anki_sdk_anki_path}";
const ANKI_SDK_AQT_PATH_PLACEHOLDER: &str = "{anki_sdk_aqt_path}";
const COMPANION_TOKEN_ENV: &str = "DECKHAND_COMPANION_TOKEN";
const MCP_REQUIRE_TOKEN_ENV: &str = "DECKHAND_MCP_REQUIRE_TOKEN";
const MCP_TOOL_TIMEOUT_ENV: &str = "DECKHAND_MCP_TOOL_TIMEOUT_SECONDS";
const DEFAULT_MCP_TOOL_TIMEOUT_SECONDS: u64 = 120;

#[derive(Debug, Clone, Serialize)]
pub struct AdapterStatus {
    pub name: &'static str,
    pub state: &'static str,
    pub detail: &'static str,
}

#[derive(Debug, Clone, Serialize)]
pub struct ServerStatus {
    pub service: &'static str,
    pub version: &'static str,
    pub ready: bool,
    pub adapters: Vec<AdapterStatus>,
    pub endpoints: Vec<&'static str>,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct McpTool {
    pub name: String,
    pub title: String,
    pub status: String,
    pub risk: String,
    pub description: String,
    pub input_schema: Value,
    pub annotations: Value,
}

#[derive(Debug, Clone, Deserialize)]
struct McpToolInventory {
    tools: Vec<McpTool>,
}

#[derive(Clone)]
pub(crate) struct BridgeHub {
    inner: Arc<BridgeHubInner>,
}

struct BridgeHubInner {
    next_id: AtomicU64,
    sender: Mutex<Option<mpsc::Sender<QueuedToolCall>>>,
    pending: Mutex<HashMap<String, oneshot::Sender<Value>>>,
    registration: Mutex<Option<Value>>,
}

#[derive(Debug)]
struct QueuedToolCall {
    id: String,
    tool: String,
    arguments: Value,
}

struct BridgeSession {
    sender: mpsc::Sender<QueuedToolCall>,
}

impl Default for BridgeHub {
    fn default() -> Self {
        Self {
            inner: Arc::new(BridgeHubInner {
                next_id: AtomicU64::new(1),
                sender: Mutex::new(None),
                pending: Mutex::new(HashMap::new()),
                registration: Mutex::new(None),
            }),
        }
    }
}

impl BridgeSession {
    fn new() -> (mpsc::Receiver<QueuedToolCall>, Self) {
        let (sender, receiver) = mpsc::channel(64);
        (receiver, Self { sender })
    }
}

impl BridgeHub {
    async fn register_session(&self, session: BridgeSession) {
        self.register_session_with_registration(session, None).await;
    }

    async fn register_session_with_registration(
        &self,
        session: BridgeSession,
        registration: Option<Value>,
    ) {
        let mut sender = self.inner.sender.lock().await;
        *sender = Some(session.sender);
        self.inner.pending.lock().await.clear();
        *self.inner.registration.lock().await = registration;
    }

    async fn tools_payload(&self) -> Value {
        let registered = self.inner.registration.lock().await.clone();
        if let Some(registration) = registered {
            let tools = anki_bridge_tools_from_registration(&registration);
            return json!({
                "server": "deckhand",
                "source": "anki_bridge",
                "protocol": registration.pointer("/params/protocolVersion").cloned().unwrap_or(Value::Null),
                "tools": tools,
            });
        }
        json!({
            "server": "deckhand",
            "source": "generated_inventory",
            "tools": mcp_tool_inventory(),
        })
    }

    async fn mcp_tools_list_payload(&self) -> Value {
        let registered = self.inner.registration.lock().await.clone();
        if let Some(registration) = registered {
            let tools = anki_bridge_tools_from_registration(&registration);
            return json!({ "tools": tools });
        }
        anki_mcp_server::tools_list_payload(&mcp_tool_inventory())
    }

    pub(crate) async fn call_tool(&self, tool: String, arguments: Value) -> Result<Value> {
        if !is_anki_bridge_tool_name(&tool) {
            return Err(anyhow!("tool_not_owned_by_anki_bridge:{tool}"));
        }
        let registered = self.inner.registration.lock().await.clone();
        if let Some(registration) = registered {
            let listed = anki_bridge_tools_from_registration(&registration);
            let visible = listed.as_array().is_some_and(|tools| {
                tools.iter().any(|entry| {
                    entry
                        .get("name")
                        .and_then(Value::as_str)
                        .is_some_and(|name| name == tool)
                })
            });
            if !visible {
                return Err(anyhow!("tool_not_advertised_by_anki_bridge:{tool}"));
            }
        }
        let sender = self
            .inner
            .sender
            .lock()
            .await
            .clone()
            .ok_or_else(|| anyhow!("anki_bridge_not_connected"))?;
        let id = format!(
            "bridge-call-{}",
            self.inner.next_id.fetch_add(1, Ordering::SeqCst)
        );
        let (reply_tx, reply_rx) = oneshot::channel();
        self.inner.pending.lock().await.insert(id.clone(), reply_tx);
        let queued = QueuedToolCall {
            id: id.clone(),
            tool,
            arguments,
        };
        let call_timeout = bridge_tool_timeout();
        if sender.send(queued).await.is_err() {
            self.inner.pending.lock().await.remove(&id);
            return Err(anyhow!("anki_bridge_disconnected"));
        }
        match timeout(call_timeout, reply_rx).await {
            Ok(Ok(value)) => Ok(value),
            Ok(Err(_)) => Err(anyhow!("anki_bridge_result_canceled")),
            Err(_) => {
                self.inner.pending.lock().await.remove(&id);
                Err(anyhow!("anki_bridge_call_timeout"))
            }
        }
    }

    async fn complete_call(&self, id: String, result: Value) {
        if let Some(sender) = self.inner.pending.lock().await.remove(&id) {
            let _ = sender.send(result);
        }
    }
}

fn bridge_tool_timeout() -> Duration {
    let seconds = env::var(MCP_TOOL_TIMEOUT_ENV)
        .ok()
        .and_then(|value| value.parse::<u64>().ok())
        .filter(|seconds| *seconds > 0)
        .unwrap_or(DEFAULT_MCP_TOOL_TIMEOUT_SECONDS);
    Duration::from_secs(seconds)
}

pub(crate) fn bridge_hub() -> BridgeHub {
    static HUB: OnceLock<BridgeHub> = OnceLock::new();
    HUB.get_or_init(BridgeHub::default).clone()
}

fn is_anki_bridge_tool_name(tool: &str) -> bool {
    tool.starts_with("anki_")
}

fn anki_bridge_tools_from_registration(registration: &Value) -> Value {
    let canonical_tools = mcp_tool_inventory()
        .into_iter()
        .map(|tool| tool.name)
        .collect::<std::collections::HashSet<_>>();
    let tools = registration
        .pointer("/params/tools")
        .or_else(|| registration.pointer("/params/capabilities/tools"))
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default()
        .into_iter()
        .filter(|tool| {
            tool.get("name")
                .and_then(Value::as_str)
                .is_some_and(|name| {
                    is_anki_bridge_tool_name(name) && canonical_tools.contains(name)
                })
        })
        .collect::<Vec<_>>();
    json!(tools)
}

fn mcp_tool_allowlist() -> Option<Vec<String>> {
    let raw = std::env::var("DECKHAND_MCP_TOOL_ALLOWLIST").ok()?;
    let tools = raw
        .split(',')
        .map(str::trim)
        .filter(|tool| !tool.is_empty())
        .map(ToString::to_string)
        .collect::<Vec<_>>();
    (!tools.is_empty()).then_some(tools)
}

fn mcp_tool_allowed(name: &str) -> bool {
    mcp_tool_allowlist()
        .as_ref()
        .is_none_or(|allowed| allowed.iter().any(|allowed_name| allowed_name == name))
}

fn default_state_root(home: Option<&str>, os: &str) -> PathBuf {
    let home = PathBuf::from(home.unwrap_or("~"));
    match os {
        "macos" => home
            .join("Library")
            .join("Application Support")
            .join("Deckhand")
            .join("state"),
        "windows" => env::var("APPDATA")
            .map(PathBuf::from)
            .unwrap_or_else(|_| home.join("AppData").join("Roaming"))
            .join("Deckhand")
            .join("state"),
        _ => env::var("XDG_DATA_HOME")
            .map(PathBuf::from)
            .unwrap_or_else(|_| home.join(".local").join("share"))
            .join("deckhand")
            .join("state"),
    }
}

pub fn status_snapshot() -> ServerStatus {
    ServerStatus {
        service: "deckhand-anki-companion",
        version: env!("CARGO_PKG_VERSION"),
        ready: true,
        adapters: vec![
            AdapterStatus {
                name: "anki_direct_executor",
                state: "waiting_for_addon",
                detail: "native add-on owns privileged direct execution",
            },
            AdapterStatus {
                name: "anki_safe_bridge",
                state: "listening",
                detail: "southbound bridge contract reserved for add-on outbound connection",
            },
        ],
        endpoints: vec!["/healthz", "/status", "/mcp", "/ws/anki"],
    }
}

async fn status_payload() -> Value {
    let tools = bridge_hub().tools_payload().await;
    let bridge_connected = tools.get("source").and_then(Value::as_str) == Some("anki_bridge");
    let tool_count = tools
        .get("tools")
        .and_then(Value::as_array)
        .map(Vec::len)
        .unwrap_or(0);
    let mut status = serde_json::to_value(status_snapshot()).unwrap_or_else(|_| json!({}));
    status["adapters"] = json!([
        {
            "name": "anki_direct_executor",
            "state": if bridge_connected { "connected" } else { "waiting_for_addon" },
            "detail": if bridge_connected {
                format!("native add-on bridge registered {tool_count} tools")
            } else {
                "native add-on owns privileged direct execution".to_string()
            },
        },
        {
            "name": "anki_safe_bridge",
            "state": if bridge_connected { "connected" } else { "listening" },
            "detail": if bridge_connected {
                "southbound bridge connected to Anki add-on".to_string()
            } else {
                "southbound bridge contract reserved for add-on outbound connection".to_string()
            },
        },
    ]);
    status["bridge"] = json!({
        "connected": bridge_connected,
        "toolCount": tool_count,
        "source": tools.get("source").cloned().unwrap_or_else(|| json!("unknown")),
    });
    status
}

pub fn mcp_tool_inventory() -> Vec<McpTool> {
    static INVENTORY: OnceLock<Vec<McpTool>> = OnceLock::new();
    let inventory = INVENTORY
        .get_or_init(|| {
            let payload: McpToolInventory =
                serde_json::from_str(include_str!("generated/mcp_tool_inventory.json"))
                    .expect("generated MCP tool inventory must be valid JSON");
            payload
                .tools
                .into_iter()
                .map(resolve_tool_description_placeholders)
                .collect()
        })
        .clone();
    inventory
        .into_iter()
        .filter(|tool| mcp_tool_allowed(&tool.name))
        .collect()
}

fn resolve_tool_description_placeholders(mut tool: McpTool) -> McpTool {
    let (anki_path, aqt_path) = anki_sdk_reference_paths();
    tool.description = tool
        .description
        .replace(ANKI_SDK_ANKI_PATH_PLACEHOLDER, &anki_path)
        .replace(ANKI_SDK_AQT_PATH_PLACEHOLDER, &aqt_path);
    tool
}

fn anki_sdk_reference_paths() -> (String, String) {
    let root = env::var("DECKHAND_ANKI_PROGRAM_FILES").unwrap_or_else(|_| {
        let home = env::var("HOME").unwrap_or_else(|_| "~".to_string());
        format!("{home}/Library/Application Support/AnkiProgramFiles")
    });
    let site_packages = format!("{root}/.venv/lib/python3.13/site-packages");
    (
        format!("{site_packages}/anki"),
        format!("{site_packages}/aqt"),
    )
}

pub async fn serve(bind: SocketAddr, parent_pid: Option<u32>) -> Result<()> {
    let listener = TcpListener::bind(bind)
        .await
        .with_context(|| format!("failed to bind Deckhand server on {bind}"))?;
    let local_addr = listener.local_addr()?;
    let (shutdown_tx, mut shutdown_rx) = mpsc::channel::<String>(4);
    if let Some(pid) = parent_pid {
        tokio::spawn(watch_parent_process(pid, shutdown_tx.clone()));
    }
    info!(%local_addr, ?parent_pid, "deckhand companion server listening");

    loop {
        tokio::select! {
            shutdown = shutdown_rx.recv() => {
                let reason = shutdown.unwrap_or_else(|| "shutdown_channel_closed".to_string());
                info!(%reason, "deckhand companion server shutting down");
                break;
            }
            accepted = listener.accept() => {
                let (stream, peer_addr) = accepted?;
                let connection_shutdown_tx = shutdown_tx.clone();
                tokio::spawn(async move {
                    if let Err(error) = handle_connection(stream, connection_shutdown_tx).await {
                        tracing::warn!(%peer_addr, %error, "request failed");
                    }
                });
            }
        }
    }
    Ok(())
}

async fn handle_connection(mut stream: TcpStream, shutdown_tx: mpsc::Sender<String>) -> Result<()> {
    let request = read_http_request(&mut stream).await?;
    let path = parse_path(&request);
    if path == "/ws/anki" && request.to_ascii_lowercase().contains("upgrade: websocket") {
        if !authorized_internal_request(&request) {
            return write_response(&mut stream, 401, "application/json", &unauthorized_body())
                .await;
        }
        return handle_anki_bridge(stream, &request, shutdown_tx).await;
    }
    if path == "/mcp" && parse_method(&request) != "OPTIONS" {
        if mcp_token_required() && !authorized_internal_request(&request) {
            return write_response(&mut stream, 401, "application/json", &unauthorized_body())
                .await;
        }
        return handle_mcp_http(stream, request).await;
    }
    let (status, content_type, body) = route_request(&request).await;
    write_response(&mut stream, status, content_type, &body).await
}

async fn read_http_request(stream: &mut TcpStream) -> Result<String> {
    let mut buffer = vec![0_u8; 65536];
    let count = stream.read(&mut buffer).await?;
    Ok(String::from_utf8_lossy(&buffer[..count]).to_string())
}

fn parse_path(request: &str) -> &str {
    request_path(request)
        .split_once('?')
        .map(|(path, _)| path)
        .unwrap_or_else(|| request_path(request))
}

fn request_path(request: &str) -> &str {
    request
        .lines()
        .next()
        .and_then(|line| line.split_whitespace().nth(1))
        .unwrap_or("/")
}

fn parse_method(request: &str) -> &str {
    request
        .lines()
        .next()
        .and_then(|line| line.split_whitespace().next())
        .unwrap_or("GET")
}

fn parse_body(request: &str) -> &str {
    request
        .split_once("\r\n\r\n")
        .map(|(_, body)| body)
        .unwrap_or("")
}

fn request_header<'a>(request: &'a str, name: &str) -> Option<&'a str> {
    let target = name.to_ascii_lowercase();
    request.lines().skip(1).find_map(|line| {
        let (key, value) = line.split_once(':')?;
        if key.trim().eq_ignore_ascii_case(&target) {
            Some(value.trim())
        } else {
            None
        }
    })
}

fn mcp_token_required() -> bool {
    env::var(MCP_REQUIRE_TOKEN_ENV).is_ok_and(|value| value == "1")
}

fn authorized_internal_request(request: &str) -> bool {
    let Ok(expected) = env::var(COMPANION_TOKEN_ENV) else {
        return true;
    };
    if expected.is_empty() {
        return false;
    }
    let bearer = request_header(request, "authorization")
        .and_then(|value| value.strip_prefix("Bearer "))
        .is_some_and(|token| token == expected);
    let explicit_header =
        request_header(request, "x-deckhand-token").is_some_and(|token| token == expected);
    bearer
        || explicit_header
        || query_param(request_path(request), "token").is_some_and(|token| token == expected)
}

fn query_param<'a>(path: &'a str, name: &str) -> Option<&'a str> {
    let (_, query) = path.split_once('?')?;
    query.split('&').find_map(|part| {
        let (key, value) = part.split_once('=').unwrap_or((part, ""));
        (key == name).then_some(value)
    })
}

fn unauthorized_body() -> Vec<u8> {
    serde_json::to_vec(&json!({ "ok": false, "error": "unauthorized" }))
        .expect("serializable unauthorized response")
}

async fn handle_mcp_http(mut stream: TcpStream, request: String) -> Result<()> {
    let response = match mcp_http_request(&request) {
        Ok(request) => {
            let mut service = anki_mcp_server::streamable_http_service();
            match service.call(request).await {
                Ok(response) => response,
                Err(never) => match never {},
            }
        }
        Err(error) => {
            let body = Full::new(Bytes::from(error.to_string())).boxed();
            http::Response::builder()
                .status(StatusCode::BAD_REQUEST)
                .body(body)
                .expect("valid response")
        }
    };
    write_http_response(&mut stream, response).await
}

fn mcp_http_request(request: &str) -> Result<Request<Full<Bytes>>> {
    let mut lines = request.lines();
    let request_line = lines
        .next()
        .ok_or_else(|| anyhow!("missing request line"))?;
    let mut request_parts = request_line.split_whitespace();
    let method = request_parts
        .next()
        .ok_or_else(|| anyhow!("missing request method"))?;
    let uri = request_parts
        .next()
        .ok_or_else(|| anyhow!("missing request uri"))?;
    let mut builder = Request::builder().method(method).uri(uri);
    for line in request
        .split_once("\r\n\r\n")
        .map(|(headers, _)| headers)
        .unwrap_or(request)
        .lines()
        .skip(1)
    {
        let Some((key, value)) = line.split_once(':') else {
            continue;
        };
        let name = HeaderName::from_bytes(key.trim().as_bytes())
            .with_context(|| format!("invalid header name: {key}"))?;
        let value = HeaderValue::from_str(value.trim())
            .with_context(|| format!("invalid header value for {key}"))?;
        builder = builder.header(name, value);
    }
    Ok(builder.body(Full::new(Bytes::copy_from_slice(
        parse_body(request).as_bytes(),
    )))?)
}

async fn write_http_response(
    stream: &mut TcpStream,
    response: http::Response<http_body_util::combinators::BoxBody<Bytes, std::convert::Infallible>>,
) -> Result<()> {
    let status = response.status();
    let reason = status.canonical_reason().unwrap_or("OK");
    let (parts, body) = response.into_parts();
    let body = body.collect().await?.to_bytes();
    let mut headers = HeaderMap::new();
    for (name, value) in parts.headers {
        if let Some(name) = name {
            headers.append(name, value);
        }
    }
    headers.insert(
        http::header::CONTENT_LENGTH,
        HeaderValue::from_str(&body.len().to_string())?,
    );
    headers.insert(http::header::CONNECTION, HeaderValue::from_static("close"));
    headers.insert(
        http::header::CACHE_CONTROL,
        HeaderValue::from_static("no-store, no-cache, must-revalidate"),
    );
    headers.insert(http::header::PRAGMA, HeaderValue::from_static("no-cache"));
    headers.insert(http::header::EXPIRES, HeaderValue::from_static("0"));
    headers.insert(
        http::header::ACCESS_CONTROL_ALLOW_ORIGIN,
        HeaderValue::from_static("*"),
    );
    headers.insert(
        http::header::ACCESS_CONTROL_ALLOW_METHODS,
        HeaderValue::from_static("GET, POST, DELETE, OPTIONS"),
    );
    headers.insert(
        http::header::ACCESS_CONTROL_ALLOW_HEADERS,
        HeaderValue::from_static(
            "content-type, accept, authorization, x-deckhand-token, mcp-session-id, mcp-protocol-version",
        ),
    );

    let mut raw = format!("HTTP/1.1 {} {reason}\r\n", status.as_u16());
    for (name, value) in headers.iter() {
        raw.push_str(name.as_str());
        raw.push_str(": ");
        raw.push_str(value.to_str().unwrap_or(""));
        raw.push_str("\r\n");
    }
    raw.push_str("\r\n");
    stream.write_all(raw.as_bytes()).await?;
    stream.write_all(&body).await?;
    stream.shutdown().await?;
    Ok(())
}

fn websocket_accept_value(request: &str) -> Result<String> {
    let key = request_header(request, "sec-websocket-key")
        .ok_or_else(|| anyhow!("missing Sec-WebSocket-Key"))?;
    let mut hasher = Sha1::new();
    hasher.update(key.as_bytes());
    hasher.update(b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11");
    Ok(base64::engine::general_purpose::STANDARD.encode(hasher.finalize()))
}

async fn route_request(request: &str) -> (u16, &'static str, Vec<u8>) {
    let path = parse_path(request);
    if parse_method(request) == "OPTIONS" {
        return (200, "application/json", b"{}".to_vec());
    }
    if path == "/status" && parse_method(request) == "GET" {
        return json_response(200, status_payload().await);
    }
    route(path)
}

fn route(path: &str) -> (u16, &'static str, Vec<u8>) {
    match path {
        "/healthz" => json_response(200, json!({ "ok": true, "ready": true })),
        "/status" => json_response(200, status_snapshot()),
        _ => json_response(404, json!({ "ok": false, "error": "not_found" })),
    }
}

async fn handle_anki_bridge(
    mut stream: TcpStream,
    request: &str,
    shutdown_tx: mpsc::Sender<String>,
) -> Result<()> {
    let accept = websocket_accept_value(request)?;
    let response = format!(
        "HTTP/1.1 101 Switching Protocols\r\n\
         Upgrade: websocket\r\n\
         Connection: Upgrade\r\n\
         Sec-WebSocket-Accept: {accept}\r\n\
         \r\n"
    );
    stream.write_all(response.as_bytes()).await?;
    log_bridge_event(json!({
        "event": "server.safe_bridge.upgraded",
        "versioned": true,
    }));

    if let Some(register) = read_ws_text(&mut stream).await? {
        let parsed: Value = serde_json::from_str(&register)?;
        if let Err(error) = validate_anki_bridge_hello(&parsed) {
            send_ws_text(
                &mut stream,
                &json!({
                    "method": "anki_bridge_reject",
                    "params": { "error": error.to_string() }
                })
                .to_string(),
            )
            .await?;
            return Err(error);
        }
        if let Some(addon_version) = bridge_addon_version(&parsed) {
            if version_is_newer(addon_version, env!("CARGO_PKG_VERSION")) {
                send_ws_text(
                    &mut stream,
                    &json!({
                        "method": "anki_bridge_reject",
                        "params": {
                            "error": "companion_takeover_newer_addon",
                            "addonVersion": addon_version,
                            "companionVersion": env!("CARGO_PKG_VERSION")
                        }
                    })
                    .to_string(),
                )
                .await?;
                let _ = shutdown_tx
                    .send("newer_anki_addon_bridge_hello".to_string())
                    .await;
                return Ok(());
            }
        }
        send_ws_text(
            &mut stream,
            &json!({
                "method": "anki_bridge_accept",
                "params": { "protocolVersion": "deckhand.ankiBridge.v1" }
            })
            .to_string(),
        )
        .await?;
        let tool_count = parsed
            .pointer("/params/tools")
            .and_then(Value::as_array)
            .map(Vec::len)
            .unwrap_or(0);
        log_bridge_event(json!({
            "event": "server.safe_bridge.registered",
            "toolCount": tool_count,
            "method": parsed.get("method"),
        }));
        let (mut requests, session) = BridgeSession::new();
        bridge_hub()
            .register_session_with_registration(session, Some(parsed))
            .await;
        loop {
            tokio::select! {
                request = requests.recv() => {
                    let Some(request) = request else { break; };
                    send_ws_text(
                        &mut stream,
                        &json!({
                            "id": request.id,
                            "method": "tool.call",
                            "params": { "tool": request.tool, "arguments": request.arguments }
                        })
                        .to_string(),
                    )
                    .await?;
                }
                message = read_ws_text(&mut stream) => {
                    let Some(message) = message? else { break; };
                    let parsed: Value = serde_json::from_str(&message)?;
                    let id = parsed.get("id").and_then(Value::as_str).unwrap_or("").to_string();
                    let tool = parsed.pointer("/params/tool").and_then(Value::as_str);
                    let ok = parsed.pointer("/params/ok").and_then(Value::as_bool);
                    let duration_ms = parsed.pointer("/params/durationMs").and_then(Value::as_i64);
                    log_bridge_event(json!({
                        "event": "server.safe_bridge.tool_result",
                        "id": id,
                        "tool": tool,
                        "ok": ok,
                        "durationMs": duration_ms,
                    }));
                    bridge_hub().complete_call(id, parsed).await;
                }
            }
        }
    }
    Ok(())
}

async fn watch_parent_process(parent_pid: u32, shutdown_tx: mpsc::Sender<String>) {
    if parent_pid == 0 {
        return;
    }
    while process_alive(parent_pid) {
        sleep(Duration::from_secs(2)).await;
    }
    let _ = shutdown_tx
        .send(format!("parent_process_exited:{parent_pid}"))
        .await;
}

fn bridge_addon_version(message: &Value) -> Option<&str> {
    message
        .pointer("/params/addonVersion")
        .and_then(Value::as_str)
}

fn version_is_newer(candidate: &str, current: &str) -> bool {
    compare_versions(candidate, current) == CmpOrdering::Greater
}

fn compare_versions(left: &str, right: &str) -> CmpOrdering {
    let left_parts = version_parts(left);
    let right_parts = version_parts(right);
    let len = left_parts.len().max(right_parts.len());
    for index in 0..len {
        let left_part = *left_parts.get(index).unwrap_or(&0);
        let right_part = *right_parts.get(index).unwrap_or(&0);
        match left_part.cmp(&right_part) {
            CmpOrdering::Equal => {}
            ordering => return ordering,
        }
    }
    CmpOrdering::Equal
}

fn version_parts(value: &str) -> Vec<u64> {
    value
        .split(|ch: char| !ch.is_ascii_digit())
        .filter(|part| !part.is_empty())
        .map(|part| part.parse::<u64>().unwrap_or(0))
        .collect()
}

#[cfg(unix)]
fn process_alive(pid: u32) -> bool {
    let result = unsafe { libc::kill(pid as libc::pid_t, 0) };
    result == 0 || std::io::Error::last_os_error().raw_os_error() == Some(libc::EPERM)
}

#[cfg(windows)]
fn process_alive(pid: u32) -> bool {
    use windows_sys::Win32::Foundation::{CloseHandle, STILL_ACTIVE};
    use windows_sys::Win32::System::Threading::{
        GetExitCodeProcess, OpenProcess, PROCESS_QUERY_LIMITED_INFORMATION,
    };

    unsafe {
        let handle = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid);
        if handle == 0 {
            return false;
        }
        let mut exit_code = 0_u32;
        let ok = GetExitCodeProcess(handle, &mut exit_code);
        CloseHandle(handle);
        ok != 0 && exit_code == STILL_ACTIVE
    }
}

fn validate_anki_bridge_hello(message: &Value) -> Result<()> {
    if message.get("method").and_then(Value::as_str) != Some("anki_bridge_hello") {
        return Err(anyhow!("invalid_anki_bridge_hello_method"));
    }
    let protocol = message
        .pointer("/params/protocolVersion")
        .and_then(Value::as_str)
        .unwrap_or_default();
    if protocol != "deckhand.ankiBridge.v1" {
        return Err(anyhow!("unsupported_anki_bridge_protocol"));
    }
    let token = message
        .pointer("/params/pairingToken")
        .and_then(Value::as_str)
        .unwrap_or_default();
    if let Ok(expected) = env::var("DECKHAND_ANKI_BRIDGE_TOKEN") {
        if expected.is_empty() || token != expected {
            return Err(anyhow!("invalid_anki_bridge_pairing_token"));
        }
    }
    let tools = message
        .pointer("/params/tools")
        .and_then(Value::as_array)
        .ok_or_else(|| anyhow!("missing_anki_bridge_tools"))?;
    if !tools.iter().any(|tool| {
        tool.get("name")
            .and_then(Value::as_str)
            .is_some_and(|name| name.starts_with("anki_"))
    }) {
        return Err(anyhow!("missing_anki_tool_registry"));
    }
    Ok(())
}

fn log_bridge_event(value: Value) {
    static LOG_LOCK: OnceLock<StdMutex<()>> = OnceLock::new();
    let Ok(path) = env::var("DECKHAND_BRIDGE_EVIDENCE_LOG") else {
        return;
    };
    let Ok(_guard) = LOG_LOCK.get_or_init(|| StdMutex::new(())).lock() else {
        return;
    };
    if let Some(parent) = std::path::Path::new(&path).parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    if let Ok(mut file) = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(path)
    {
        use std::io::Write;
        let _ = writeln!(file, "{}", value);
    }
}

async fn read_ws_text(stream: &mut TcpStream) -> Result<Option<String>> {
    let mut header = [0_u8; 2];
    if stream.read_exact(&mut header).await.is_err() {
        return Ok(None);
    }
    let opcode = header[0] & 0x0f;
    if opcode == 0x8 {
        return Ok(None);
    }
    let masked = header[1] & 0x80 != 0;
    let mut len = u64::from(header[1] & 0x7f);
    if len == 126 {
        let mut bytes = [0_u8; 2];
        stream.read_exact(&mut bytes).await?;
        len = u64::from(u16::from_be_bytes(bytes));
    } else if len == 127 {
        let mut bytes = [0_u8; 8];
        stream.read_exact(&mut bytes).await?;
        len = u64::from_be_bytes(bytes);
    }
    let mut mask = [0_u8; 4];
    if masked {
        stream.read_exact(&mut mask).await?;
    }
    let mut payload = vec![0_u8; len as usize];
    stream.read_exact(&mut payload).await?;
    if masked {
        for (index, byte) in payload.iter_mut().enumerate() {
            *byte ^= mask[index % 4];
        }
    }
    Ok(Some(String::from_utf8_lossy(&payload).to_string()))
}

async fn send_ws_text(stream: &mut TcpStream, text: &str) -> Result<()> {
    let payload = text.as_bytes();
    let mut frame = vec![0x81];
    if payload.len() < 126 {
        frame.push(payload.len() as u8);
    } else if payload.len() <= 0xffff {
        frame.push(126);
        frame.extend_from_slice(&(payload.len() as u16).to_be_bytes());
    } else {
        frame.push(127);
        frame.extend_from_slice(&(payload.len() as u64).to_be_bytes());
    }
    frame.extend_from_slice(payload);
    stream.write_all(&frame).await?;
    Ok(())
}

fn json_response<T: Serialize>(status: u16, value: T) -> (u16, &'static str, Vec<u8>) {
    let body = serde_json::to_vec_pretty(&value).expect("serializable response");
    (status, "application/json", body)
}

async fn write_response(
    stream: &mut TcpStream,
    status: u16,
    content_type: &str,
    body: &[u8],
) -> Result<()> {
    let reason = match status {
        200 => "OK",
        401 => "Unauthorized",
        404 => "Not Found",
        _ => "OK",
    };
    let headers = format!(
        "HTTP/1.1 {status} {reason}\r\ncontent-type: {content_type}\r\ncontent-length: {}\r\ncache-control: no-store, no-cache, must-revalidate\r\npragma: no-cache\r\nexpires: 0\r\naccess-control-allow-origin: *\r\naccess-control-allow-methods: GET, POST, DELETE, OPTIONS\r\naccess-control-allow-headers: content-type, accept, authorization, x-deckhand-token, mcp-session-id, mcp-protocol-version\r\nconnection: close\r\n\r\n",
        body.len()
    );
    stream.write_all(headers.as_bytes()).await?;
    stream.write_all(body).await?;
    stream.shutdown().await?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn status_lists_required_endpoints_and_adapters() {
        let status = status_snapshot();
        assert!(status.ready);
        assert!(status.endpoints.contains(&"/healthz"));
        assert!(status.endpoints.contains(&"/mcp"));
        assert!(status.endpoints.contains(&"/ws/anki"));
        assert!(!status.endpoints.contains(&"/api/app/state"));
        assert!(!status.endpoints.contains(&"/anki-bridge"));
        assert!(!status.endpoints.contains(&"/events"));
        assert!(status
            .adapters
            .iter()
            .any(|adapter| adapter.name == "anki_safe_bridge"));
    }

    #[test]
    fn mcp_inventory_advertises_expected_namespaces() {
        let inventory = mcp_tool_inventory();
        let names = inventory
            .iter()
            .map(|tool| tool.name.as_str())
            .collect::<std::collections::BTreeSet<_>>();

        assert_eq!(
            names,
            ["anki_backup_create", "anki_run_python", "anki_runtime_info"]
                .into_iter()
                .collect()
        );
        assert!(inventory
            .iter()
            .all(|tool| { tool.status == "implemented" && tool.name.starts_with("anki_") }));
        assert!(inventory
            .iter()
            .all(|tool| tool.name != "anki_context_get_current"));
        assert!(inventory.iter().all(|tool| {
            !tool.name.starts_with("anki_bridge_")
                && !tool.name.starts_with("anki_smoke_")
                && !tool.name.starts_with("system.")
                && !tool.name.starts_with("ui.sidebar.")
                && !tool.name.starts_with("anki_navigate_")
                && !tool.name.starts_with("anki_template_")
                && !tool.name.starts_with("anki_import_")
                && !matches!(
                    tool.name.as_str(),
                    "anki_backup_collection"
                        | "anki_dev_sql_read"
                        | "anki_dev_list_hooks"
                        | "anki_dev_get_addon_config"
                        | "anki_dev_diagnostics"
                )
        }));
        assert!(inventory.iter().all(|tool| {
            !tool.description.contains(ANKI_SDK_ANKI_PATH_PLACEHOLDER)
                && !tool.description.contains(ANKI_SDK_AQT_PATH_PLACEHOLDER)
        }));
    }

    #[test]
    fn bridge_registration_uses_fixed_public_tool_set() {
        let tools = anki_bridge_tools_from_registration(&json!({
            "params": {
                "tools": [
                    { "name": "anki_app_get_state" },
                    { "name": "anki_backup_create" },
                    { "name": "anki_run_python" },
                    { "name": "anki_runtime_info" },
                    { "name": "anki_webengine_status" },
                    { "name": "other.exec.run" }
                ]
            }
        }));
        let names = tools
            .as_array()
            .unwrap()
            .iter()
            .filter_map(|tool| tool.get("name").and_then(Value::as_str))
            .collect::<std::collections::BTreeSet<_>>();

        assert_eq!(
            names,
            ["anki_backup_create", "anki_run_python", "anki_runtime_info"]
                .into_iter()
                .collect()
        );
    }

    #[test]
    fn routes_health_status_and_removed_placeholders() {
        let health = route("/healthz");
        assert_eq!(health.0, 200);
        assert_eq!(health.1, "application/json");

        assert_eq!(route("/embed/anki").0, 404);
        assert_eq!(route("/app").0, 404);
        assert_eq!(route("/events").0, 404);
        assert_eq!(route("/anki-bridge/status").0, 404);
    }

    #[tokio::test]
    async fn status_payload_exposes_bridge_without_legacy_app_state() {
        let payload = status_payload().await;
        assert_eq!(payload["bridge"]["connected"], false);
        assert!(payload.get("approvals").is_none());
        assert!(payload.get("mcp").is_none());
        assert!(payload.get("toolEvents").is_none());
        assert!(payload.get("chat").is_none());
        assert!(payload.get("account").is_none());
        assert!(payload.get("models").is_none());
    }

    #[test]
    fn bridge_tool_timeout_defaults_to_codex_mcp_duration() {
        std::env::remove_var(MCP_TOOL_TIMEOUT_ENV);
        assert_eq!(bridge_tool_timeout(), Duration::from_secs(120));
    }

    #[test]
    fn bridge_tool_timeout_uses_one_duration_for_all_tools() {
        std::env::remove_var(MCP_TOOL_TIMEOUT_ENV);
        assert_eq!(bridge_tool_timeout(), Duration::from_secs(120));
        assert_eq!(bridge_tool_timeout(), Duration::from_secs(120));
    }

    #[test]
    fn bridge_tool_timeout_accepts_positive_env_override() {
        std::env::set_var(MCP_TOOL_TIMEOUT_ENV, "5");
        assert_eq!(bridge_tool_timeout(), Duration::from_secs(5));
        std::env::remove_var(MCP_TOOL_TIMEOUT_ENV);
    }

    #[test]
    fn bridge_tool_timeout_ignores_invalid_env_override() {
        for value in ["abc", "0", "-1"] {
            std::env::set_var(MCP_TOOL_TIMEOUT_ENV, value);
            assert_eq!(
                bridge_tool_timeout(),
                Duration::from_secs(DEFAULT_MCP_TOOL_TIMEOUT_SECONDS)
            );
        }
        std::env::remove_var(MCP_TOOL_TIMEOUT_ENV);
    }

    #[test]
    fn websocket_accept_matches_rfc6455_example() {
        let request = concat!(
            "GET /ws/anki HTTP/1.1\r\n",
            "Host: 127.0.0.1:28765\r\n",
            "Upgrade: websocket\r\n",
            "Connection: Upgrade\r\n",
            "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n",
            "Sec-WebSocket-Version: 13\r\n",
            "\r\n"
        );

        assert_eq!(
            websocket_accept_value(request).unwrap(),
            "s3pPLMBiTxaQ9kYGzzhZRbK+xOo="
        );
    }

    #[test]
    fn companion_token_authorizes_internal_requests() {
        std::env::set_var(COMPANION_TOKEN_ENV, "secret-token");
        let missing = concat!(
            "GET /ws/anki HTTP/1.1\r\n",
            "Host: 127.0.0.1:28765\r\n",
            "\r\n"
        );
        assert!(!authorized_internal_request(missing));

        let bearer = concat!(
            "GET /ws/anki HTTP/1.1\r\n",
            "Host: 127.0.0.1:28765\r\n",
            "Authorization: Bearer secret-token\r\n",
            "\r\n"
        );
        assert!(authorized_internal_request(bearer));

        let query = concat!(
            "GET /ws/anki?token=secret-token HTTP/1.1\r\n",
            "Host: 127.0.0.1:28765\r\n",
            "\r\n"
        );
        assert!(authorized_internal_request(query));
        std::env::remove_var(COMPANION_TOKEN_ENV);
    }

    #[test]
    fn default_state_root_is_platform_appropriate() {
        let mac = default_state_root(Some("/Users/example"), "macos");
        assert_eq!(
            mac,
            PathBuf::from("/Users/example/Library/Application Support/Deckhand/state")
        );

        std::env::remove_var("XDG_DATA_HOME");
        let linux = default_state_root(Some("/home/example"), "linux");
        assert_eq!(
            linux,
            PathBuf::from("/home/example/.local/share/deckhand/state")
        );
    }

    #[test]
    fn mcp_token_requirement_follows_env_flag() {
        std::env::remove_var(MCP_REQUIRE_TOKEN_ENV);
        assert!(!mcp_token_required());

        std::env::set_var(MCP_REQUIRE_TOKEN_ENV, "0");
        assert!(!mcp_token_required());

        std::env::set_var(MCP_REQUIRE_TOKEN_ENV, "1");
        assert!(mcp_token_required());
        std::env::remove_var(MCP_REQUIRE_TOKEN_ENV);
    }

    #[tokio::test]
    async fn removed_app_state_route_is_not_exposed() {
        std::env::set_var(COMPANION_TOKEN_ENV, "secret-token");
        let missing = route_request(concat!(
            "GET /api/app/state HTTP/1.1\r\n",
            "Host: 127.0.0.1:28765\r\n",
            "\r\n"
        ))
        .await;
        assert_eq!(missing.0, 404);

        let still_missing_with_token = route_request(concat!(
            "GET /api/app/state HTTP/1.1\r\n",
            "Host: 127.0.0.1:28765\r\n",
            "Authorization: Bearer secret-token\r\n",
            "\r\n"
        ))
        .await;
        assert_eq!(still_missing_with_token.0, 404);

        let status = route_request(concat!(
            "GET /status HTTP/1.1\r\n",
            "Host: 127.0.0.1:28765\r\n",
            "\r\n"
        ))
        .await;
        assert_eq!(status.0, 200);
        std::env::remove_var(COMPANION_TOKEN_ENV);
    }

    #[test]
    fn validates_versioned_anki_bridge_hello_and_pairing_token() {
        std::env::set_var("DECKHAND_ANKI_BRIDGE_TOKEN", "secret");
        let valid = json!({
            "method": "anki_bridge_hello",
            "params": {
                "protocolVersion": "deckhand.ankiBridge.v1",
                "pairingToken": "secret",
                "tools": [{ "name": "anki_app_get_state" }]
            }
        });
        assert!(validate_anki_bridge_hello(&valid).is_ok());

        let invalid = json!({
            "method": "anki_bridge_hello",
            "params": {
                "protocolVersion": "deckhand.ankiBridge.v1",
                "pairingToken": "wrong",
                "tools": [{ "name": "anki_app_get_state" }]
            }
        });
        assert!(validate_anki_bridge_hello(&invalid)
            .unwrap_err()
            .to_string()
            .contains("pairing_token"));
        std::env::remove_var("DECKHAND_ANKI_BRIDGE_TOKEN");
    }

    #[test]
    fn compares_addon_versions_for_graceful_takeover() {
        assert!(version_is_newer("0.1.12", "0.1.11"));
        assert!(version_is_newer("0.2.0", "0.1.99"));
        assert!(!version_is_newer("0.1.11", "0.1.11"));
        assert!(!version_is_newer("0.1.10", "0.1.11"));
        assert_eq!(compare_versions("0.1.11", "0.1.11"), CmpOrdering::Equal);
    }

    #[test]
    fn reads_addon_version_from_bridge_hello() {
        let message = json!({
            "method": "anki_bridge_hello",
            "params": {
                "protocolVersion": "deckhand.ankiBridge.v1",
                "addonVersion": "0.1.12",
                "tools": [{ "name": "anki_runtime_info" }]
            }
        });

        assert_eq!(bridge_addon_version(&message), Some("0.1.12"));
    }

    #[tokio::test]
    async fn api_mcp_tools_prefers_live_anki_bridge_registry() {
        let hub = BridgeHub::default();
        let (_requests, session) = BridgeSession::new();
        hub.register_session_with_registration(
            session,
            Some(json!({
                "method": "anki_bridge_hello",
                "params": {
                    "protocolVersion": "deckhand.ankiBridge.v1",
                    "tools": [
                        { "name": "anki_app_get_state", "risk": "read" },
                        { "name": "anki_backup_create", "risk": "mutation" },
                        { "name": "anki_run_python", "risk": "dev_exec" },
                        { "name": "anki_runtime_info", "risk": "read" },
                        { "name": "anki_webengine_status", "risk": "read" },
                        { "name": "other.exec.run", "risk": "system_exec" },
                        { "name": "other.exec.run", "risk": "system_exec" },
                        { "name": "other.sidebar.show_status", "risk": "ui" }
                    ]
                }
            })),
        )
        .await;

        let payload = hub.tools_payload().await;
        assert_eq!(payload["source"], "anki_bridge");
        assert_eq!(payload["protocol"], "deckhand.ankiBridge.v1");
        assert_eq!(payload["tools"].as_array().unwrap().len(), 3);
        assert_eq!(payload["tools"][0]["name"], "anki_backup_create");
        assert_eq!(payload["tools"][1]["name"], "anki_run_python");
        assert_eq!(payload["tools"][2]["name"], "anki_runtime_info");

        let mcp_payload = hub.mcp_tools_list_payload().await;
        assert_eq!(mcp_payload["tools"].as_array().unwrap().len(), 3);
        assert_eq!(mcp_payload["tools"][0]["name"], "anki_backup_create");
        assert_eq!(mcp_payload["tools"][1]["name"], "anki_run_python");
        assert_eq!(mcp_payload["tools"][2]["name"], "anki_runtime_info");
    }

    #[test]
    fn bridge_event_logging_preserves_json_lines_under_concurrency() {
        let path = std::env::temp_dir().join(format!(
            "deckhand-bridge-log-{}-{}.jsonl",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        std::env::set_var("DECKHAND_BRIDGE_EVIDENCE_LOG", &path);

        let workers = (0..8)
            .map(|worker| {
                std::thread::spawn(move || {
                    for index in 0..25 {
                        log_bridge_event(json!({
                            "event": "test.concurrent_log",
                            "worker": worker,
                            "index": index,
                        }));
                    }
                })
            })
            .collect::<Vec<_>>();
        for worker in workers {
            worker.join().unwrap();
        }

        let content = std::fs::read_to_string(&path).unwrap();
        let lines = content.lines().collect::<Vec<_>>();
        assert!(lines.len() >= 200);
        let mut concurrent_events = 0;
        for line in lines {
            let parsed: Value = serde_json::from_str(line).unwrap();
            if parsed["event"] == "test.concurrent_log" {
                concurrent_events += 1;
            }
        }
        assert_eq!(concurrent_events, 200);
        std::env::remove_var("DECKHAND_BRIDGE_EVIDENCE_LOG");
        let _ = std::fs::remove_file(path);
    }

    #[tokio::test]
    async fn bridge_hub_rejects_calls_without_connected_addon() {
        let hub = BridgeHub::default();

        let result = hub
            .call_tool("anki_context_get_profile".to_string(), json!({}))
            .await;

        assert!(result.is_err());
    }

    #[tokio::test]
    async fn bridge_hub_rejects_non_anki_owned_tools() {
        let hub = BridgeHub::default();
        let (_requests, session) = BridgeSession::new();
        hub.register_session(session).await;

        let result = hub.call_tool("other.exec.run".to_string(), json!({})).await;

        assert!(result
            .unwrap_err()
            .to_string()
            .contains("tool_not_owned_by_anki_bridge"));
    }

    #[tokio::test]
    async fn bridge_hub_rejects_unadvertised_anki_tools() {
        let hub = BridgeHub::default();
        let (_requests, session) = BridgeSession::new();
        hub.register_session_with_registration(
            session,
            Some(json!({
                "params": {
                    "tools": [{ "name": "anki_run_python" }]
                }
            })),
        )
        .await;

        let result = hub
            .call_tool("anki_note_search".to_string(), json!({}))
            .await;

        assert!(result
            .unwrap_err()
            .to_string()
            .contains("tool_not_advertised_by_anki_bridge"));
    }

    #[tokio::test]
    async fn bridge_hub_completes_queued_tool_call_from_result() {
        let hub = BridgeHub::default();
        let (mut requests, session) = BridgeSession::new();
        hub.register_session(session).await;

        let pending = tokio::spawn({
            let hub = hub.clone();
            async move {
                hub.call_tool("anki_context_get_profile".to_string(), json!({}))
                    .await
                    .unwrap()
            }
        });
        let request = requests.recv().await.unwrap();
        assert_eq!(request.tool, "anki_context_get_profile");

        hub.complete_call(
            request.id.clone(),
            json!({
                "id": request.id,
                "method": "tool.result",
                "params": {
                    "tool": "anki_context_get_profile",
                    "ok": true,
                    "result": { "profile": "Test User" },
                    "error": null,
                    "durationMs": 7
                }
            }),
        )
        .await;

        let result = pending.await.unwrap();
        assert_eq!(
            result.pointer("/params/ok").and_then(Value::as_bool),
            Some(true)
        );
        assert_eq!(
            result.pointer("/params/durationMs").and_then(Value::as_i64),
            Some(7)
        );
    }

    #[tokio::test]
    async fn bridge_hub_preserves_tool_arguments_without_server_injection() {
        let hub = BridgeHub::default();
        let (mut requests, session) = BridgeSession::new();
        hub.register_session(session).await;

        let pending = tokio::spawn({
            let hub = hub.clone();
            async move {
                hub.call_tool(
                    "anki_note_update_fields".to_string(),
                    json!({
                        "note_id": 123,
                        "fields": { "Back": "direct over server route" }
                    }),
                )
                .await
                .unwrap()
            }
        });
        let request = requests.recv().await.unwrap();
        assert_eq!(request.tool, "anki_note_update_fields");
        assert_eq!(
            request.arguments.get("note_id").and_then(Value::as_i64),
            Some(123)
        );

        hub.complete_call(
            request.id.clone(),
            json!({
                "id": request.id,
                "method": "tool.result",
                "params": {
                    "tool": "anki_note_update_fields",
                    "ok": true,
                    "result": { "noteId": 123, "updatedFields": ["Back"] },
                    "error": null,
                    "durationMs": 5
                }
            }),
        )
        .await;

        let result = pending.await.unwrap();
        assert_eq!(
            result.pointer("/params/ok").and_then(Value::as_bool),
            Some(true)
        );
    }

    #[tokio::test]
    async fn websocket_text_frames_round_trip_locally() {
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();
        let client = tokio::spawn(async move {
            let mut stream = TcpStream::connect(addr).await.unwrap();
            send_ws_text(&mut stream, r#"{"hello":"world"}"#)
                .await
                .unwrap();
        });
        let (mut server, _) = listener.accept().await.unwrap();
        let text = read_ws_text(&mut server).await.unwrap().unwrap();
        client.await.unwrap();

        assert!(text.contains("world"));
    }
}
