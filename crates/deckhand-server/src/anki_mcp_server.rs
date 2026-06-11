use rmcp::{
    model::{
        CallToolRequestParams, CallToolResult, Content, ErrorData as McpError, Implementation,
        JsonObject, ListToolsResult, PaginatedRequestParams, ServerCapabilities, ServerInfo, Tool,
        ToolAnnotations,
    },
    service::RequestContext,
    transport::{StreamableHttpServerConfig, StreamableHttpService},
    RoleServer, ServerHandler,
};
use serde_json::{json, Value};
use std::{
    borrow::Cow,
    sync::{Arc, OnceLock},
};

use crate::server_shell::{bridge_hub, mcp_tool_inventory, BridgeHub, McpTool};

pub const MCP_SERVER_NAME: &str = "deckhand";
pub const MCP_SERVER_INSTRUCTIONS: &str = "Deckhand controls a live, running Anki instance. Use anki_run_python for anything involving cards, decks, notes, reviews, quizzes, stats, add-ons, or inspecting and driving Anki's UI; it runs Python inside Anki with mw/aqt access and includes deckhand.web for screenshots, clicking, and reading rendered cards. Use anki_runtime_info for a quick Anki status or health check.";

pub fn tools_list_payload(tools: &[McpTool]) -> Value {
    json!({
        "tools": tools.iter().map(mcp_tool_json).collect::<Vec<_>>(),
        "nextCursor": null,
    })
}

pub fn mcp_tool_json(tool: &McpTool) -> Value {
    json!({
        "name": tool.name,
        "title": tool.title,
        "description": tool.description,
        "inputSchema": tool.input_schema,
        "annotations": tool.annotations,
    })
}

pub fn mcp_tool_model(tool: &McpTool) -> Tool {
    let read_only = annotation_bool(&tool.annotations, "readOnlyHint");
    let destructive = annotation_bool(&tool.annotations, "destructiveHint");
    let idempotent = annotation_bool(&tool.annotations, "idempotentHint");
    let open_world = annotation_bool(&tool.annotations, "openWorldHint");
    Tool::new(
        Cow::Owned(tool.name.clone()),
        Cow::Owned(tool.description.clone()),
        Arc::new(json_object(tool.input_schema.clone())),
    )
    .with_title(tool.title.clone())
    .with_annotations(ToolAnnotations::from_raw(
        Some(tool.title.clone()),
        Some(read_only),
        Some(destructive),
        Some(idempotent),
        Some(open_world),
    ))
}

fn annotation_bool(annotations: &Value, key: &str) -> bool {
    annotations
        .get(key)
        .and_then(Value::as_bool)
        .unwrap_or(false)
}

pub fn is_anki_mcp_tool(tool: &str) -> bool {
    tool.starts_with("anki_")
}

type DeckhandHttpMcpService = StreamableHttpService<
    DeckhandMcpServer,
    rmcp::transport::streamable_http_server::session::local::LocalSessionManager,
>;

pub fn streamable_http_service() -> DeckhandHttpMcpService {
    static SERVICE: OnceLock<DeckhandHttpMcpService> = OnceLock::new();
    SERVICE.get_or_init(new_streamable_http_service).clone()
}

fn new_streamable_http_service() -> DeckhandHttpMcpService {
    StreamableHttpService::new(
        || Ok(DeckhandMcpServer::default()),
        Arc::new(
            rmcp::transport::streamable_http_server::session::local::LocalSessionManager::default(),
        ),
        StreamableHttpServerConfig::default().with_json_response(true),
    )
}

#[derive(Clone)]
pub struct DeckhandMcpServer {
    hub: BridgeHub,
}

impl Default for DeckhandMcpServer {
    fn default() -> Self {
        Self { hub: bridge_hub() }
    }
}

impl ServerHandler for DeckhandMcpServer {
    fn get_info(&self) -> ServerInfo {
        ServerInfo::new(ServerCapabilities::builder().enable_tools().build())
            .with_server_info(Implementation::new(
                MCP_SERVER_NAME,
                env!("CARGO_PKG_VERSION"),
            ))
            .with_instructions(MCP_SERVER_INSTRUCTIONS)
    }

    async fn list_tools(
        &self,
        _request: Option<PaginatedRequestParams>,
        _context: RequestContext<RoleServer>,
    ) -> Result<ListToolsResult, McpError> {
        Ok(ListToolsResult {
            tools: mcp_tool_inventory()
                .iter()
                .map(mcp_tool_model)
                .collect::<Vec<_>>(),
            ..Default::default()
        })
    }

    async fn call_tool(
        &self,
        request: CallToolRequestParams,
        _context: RequestContext<RoleServer>,
    ) -> Result<CallToolResult, McpError> {
        call_mcp_tool(
            self.hub.clone(),
            request.name.to_string(),
            arguments_value(request.arguments),
        )
        .await
        .map_err(|error| McpError::internal_error(error.to_string(), None))
    }
}

fn arguments_value(arguments: Option<JsonObject>) -> Value {
    arguments.map(Value::Object).unwrap_or_else(|| json!({}))
}

async fn call_mcp_tool(
    hub: BridgeHub,
    tool: String,
    arguments: Value,
) -> anyhow::Result<CallToolResult> {
    if !is_anki_mcp_tool(&tool) {
        anyhow::bail!("unsupported Deckhand MCP tool: {tool}");
    }

    let result = hub.call_tool(tool.clone(), arguments).await?;
    call_tool_result(&tool, result)
}

fn json_object(value: Value) -> JsonObject {
    match value {
        Value::Object(map) => map,
        _ => JsonObject::new(),
    }
}

fn call_tool_result(tool: &str, value: Value) -> anyhow::Result<CallToolResult> {
    let params = value
        .get("params")
        .and_then(Value::as_object)
        .ok_or_else(|| anyhow::anyhow!("malformed_anki_bridge_tool_result"))?;
    let ok = params
        .get("ok")
        .and_then(Value::as_bool)
        .ok_or_else(|| anyhow::anyhow!("malformed_anki_bridge_tool_result"))?;
    if ok {
        let payload = params.get("result").cloned().unwrap_or(Value::Null);
        let summary = tool_success_summary(tool, &payload);
        Ok(structured_tool_result(payload, false, summary))
    } else {
        let error = params
            .get("error")
            .and_then(Value::as_str)
            .unwrap_or("anki_tool_failed")
            .to_string();
        let payload = json!({ "tool": tool, "error": error });
        Ok(structured_tool_result(
            payload,
            true,
            format!("{tool}: {error}"),
        ))
    }
}

fn structured_tool_result(payload: Value, is_error: bool, summary: String) -> CallToolResult {
    let mut result = if is_error {
        CallToolResult::structured_error(payload.clone())
    } else {
        CallToolResult::structured(payload.clone())
    };
    result.content = vec![Content::text(tool_text_content(&summary, &payload))];
    result
}

fn tool_text_content(summary: &str, payload: &Value) -> String {
    if payload.is_null() {
        return summary.to_string();
    }
    match serde_json::to_string_pretty(payload) {
        Ok(rendered) => format!("{summary}\n\n```json\n{rendered}\n```"),
        Err(_) => summary.to_string(),
    }
}

fn tool_success_summary(tool: &str, payload: &Value) -> String {
    if let Some(path) = payload.pointer("/artifact/path").and_then(Value::as_str) {
        return format!("{tool}: wrote {path}");
    }
    if let Some(path) = payload.get("path").and_then(Value::as_str) {
        return format!("{tool}: wrote {path}");
    }
    if let Some(count) = payload.get("count").and_then(Value::as_i64) {
        return format!("{tool}: {count} result(s)");
    }
    if let Some(count) = payload.get("toolCount").and_then(Value::as_i64) {
        return format!("{tool}: {count} tool(s)");
    }
    format!("{tool}: ok")
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::server_shell::mcp_tool_inventory_for_visibility_path;
    use std::path::PathBuf;

    fn missing_visibility_path() -> PathBuf {
        std::env::temp_dir().join(format!(
            "deckhand-rmcp-missing-visibility-{}-{}.json",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ))
    }

    #[test]
    fn mcp_projection_uses_standard_annotations_without_approval_inputs() {
        let tools = mcp_tool_inventory_for_visibility_path(&missing_visibility_path());
        let create = tools
            .iter()
            .find(|tool| tool.name == "anki_note_create")
            .unwrap();
        let execute = tools
            .iter()
            .find(|tool| tool.name == "anki_run_python")
            .unwrap();
        let deck_list = tools
            .iter()
            .find(|tool| tool.name == "anki_deck_list")
            .unwrap();

        assert_eq!(create.annotations["readOnlyHint"], false);
        assert_eq!(create.annotations["destructiveHint"], false);
        assert_eq!(execute.annotations["destructiveHint"], true);
        assert_eq!(deck_list.annotations["readOnlyHint"], true);
        assert_eq!(deck_list.annotations["idempotentHint"], true);
        assert!(create.input_schema["properties"].get("approved").is_none());
        assert!(execute.input_schema["properties"].get("approved").is_none());
        assert!(execute.input_schema["properties"]
            .get("resultFilePath")
            .is_some());
        assert!(is_anki_mcp_tool("anki_app_get_state"));
        assert!(is_anki_mcp_tool("anki_run_python"));
        assert!(!is_anki_mcp_tool("other.exec.run"));
        assert!(!is_anki_mcp_tool("anki.note.search"));
    }

    #[test]
    fn rmcp_tool_models_advertise_canonical_underscore_names() {
        let tools = mcp_tool_inventory_for_visibility_path(&missing_visibility_path());
        let models = tools.iter().map(mcp_tool_model).collect::<Vec<_>>();
        let names = models
            .iter()
            .map(|tool| tool.name.as_ref())
            .collect::<std::collections::BTreeSet<_>>();

        assert!(models.len() > 20);
        assert!(names.contains("anki_app_get_state"));
        assert!(!names.contains("anki_context_get_current"));
        assert!(names.contains("anki_note_search"));
        assert!(names.contains("anki_run_python"));
        assert!(names.iter().all(|name| name
            .chars()
            .all(|ch| ch.is_ascii_alphanumeric() || ch == '_' || ch == '-')));

        let search = models
            .iter()
            .find(|tool| tool.name.as_ref() == "anki_note_search")
            .unwrap();
        assert_eq!(search.title.as_deref(), Some("Search"));
        assert_eq!(
            search.annotations.as_ref().unwrap().read_only_hint,
            Some(true)
        );
        assert_eq!(
            search.annotations.as_ref().unwrap().open_world_hint,
            Some(false)
        );
    }

    #[test]
    fn deckhand_rmcp_server_info_advertises_tools() {
        let info = DeckhandMcpServer::default().get_info();

        assert_eq!(info.server_info.name, MCP_SERVER_NAME);
        assert!(info.capabilities.tools.is_some());
        assert_eq!(info.instructions.as_deref(), Some(MCP_SERVER_INSTRUCTIONS));
    }

    #[tokio::test]
    async fn rmcp_call_rejects_non_anki_tool() {
        let error = call_mcp_tool(
            BridgeHub::default(),
            "other.exec.run".to_string(),
            json!({"command": "echo no"}),
        )
        .await
        .unwrap_err()
        .to_string();

        assert!(error.contains("unsupported Deckhand MCP tool"));
    }

    #[tokio::test]
    async fn rmcp_call_accepts_canonical_underscore_tool_names() {
        let error = call_mcp_tool(
            BridgeHub::default(),
            "anki_run_python".to_string(),
            json!({"deck":"Deckhand Smoke","model":"Basic","fields":{"Front":"rmcp","Back":"preview"}}),
        )
        .await
        .unwrap_err()
        .to_string();

        assert!(!error.is_empty());
        assert!(!error.contains("unsupported Deckhand MCP tool"));
    }

    #[tokio::test]
    async fn rmcp_mutation_routes_directly_to_bridge_without_approval_preview() {
        let error = call_mcp_tool(
            BridgeHub::default(),
            "anki_note_create".to_string(),
            json!({"deck":"Deckhand Smoke","model":"Basic","fields":{"Front":"rmcp","Back":"preview"}}),
        )
        .await
        .unwrap_err()
        .to_string();

        assert!(!error.is_empty());
        assert!(!error.contains("requiresApproval"));
        assert!(!error.contains("elicitation"));
    }

    #[test]
    fn rmcp_call_unwraps_bridge_success_envelope() {
        let result = call_tool_result(
            "anki_note_search",
            json!({
                "id": "bridge-call-1",
                "method": "tool.result",
                "params": {
                    "tool": "anki_note_search",
                    "ok": true,
                    "result": { "query": "deckhand", "noteIds": [1, 2], "count": 2 },
                    "error": null,
                    "durationMs": 9
                }
            }),
        )
        .unwrap();
        let structured = result.structured_content.unwrap();

        assert_eq!(result.is_error, Some(false));
        assert_eq!(structured["count"], 2);
        assert!(structured.get("params").is_none());
        assert!(structured.get("method").is_none());
        assert!(structured.get("ok").is_none());
        assert!(structured.get("result").is_none());
        assert!(structured.get("error").is_none());
        assert!(structured.get("durationMs").is_none());
        assert_eq!(
            serde_json::to_value(&result.content).unwrap()[0]["text"],
            "anki_note_search: 2 result(s)\n\n```json\n{\n  \"count\": 2,\n  \"noteIds\": [\n    1,\n    2\n  ],\n  \"query\": \"deckhand\"\n}\n```"
        );
    }

    #[test]
    fn rmcp_call_converts_bridge_failure_to_tool_error() {
        let result = call_tool_result(
            "anki_note_get",
            json!({
                "id": "bridge-call-2",
                "method": "tool.result",
                "params": {
                    "tool": "anki_note_get",
                    "ok": false,
                    "result": null,
                    "error": "execution_failed: missing note",
                    "durationMs": 3
                }
            }),
        )
        .unwrap();
        let structured = result.structured_content.unwrap();

        assert_eq!(result.is_error, Some(true));
        assert_eq!(structured["tool"], "anki_note_get");
        assert_eq!(structured["error"], "execution_failed: missing note");
        assert!(structured.get("durationMs").is_none());
        assert_eq!(
            serde_json::to_value(&result.content).unwrap()[0]["text"],
            "anki_note_get: execution_failed: missing note\n\n```json\n{\n  \"error\": \"execution_failed: missing note\",\n  \"tool\": \"anki_note_get\"\n}\n```"
        );
    }

    #[test]
    fn rmcp_call_rejects_malformed_bridge_result() {
        let error = call_tool_result(
            "anki_note_get",
            json!({"params": {"tool": "anki_note_get"}}),
        )
        .unwrap_err()
        .to_string();

        assert_eq!(error, "malformed_anki_bridge_tool_result");
    }
}
