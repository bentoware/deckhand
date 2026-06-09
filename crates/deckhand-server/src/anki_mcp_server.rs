use rmcp::{
    model::{
        CallToolRequestParams, CallToolResult, ErrorData as McpError, Implementation, JsonObject,
        ListToolsResult, PaginatedRequestParams, ServerCapabilities, ServerInfo, Tool,
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
    tool.starts_with("anki.")
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
        ServerInfo::new(ServerCapabilities::builder().enable_tools().build()).with_server_info(
            Implementation::new(MCP_SERVER_NAME, env!("CARGO_PKG_VERSION")),
        )
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

    let result = hub.call_tool(tool, arguments).await?;
    Ok(call_tool_result(result))
}

fn json_object(value: Value) -> JsonObject {
    match value {
        Value::Object(map) => map,
        _ => JsonObject::new(),
    }
}

fn call_tool_result(value: Value) -> CallToolResult {
    let ok = value.pointer("/params/result/ok").and_then(Value::as_bool);
    if ok == Some(false) {
        CallToolResult::structured_error(value)
    } else {
        CallToolResult::structured(value)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn mcp_projection_uses_standard_annotations_without_approval_inputs() {
        let tools = mcp_tool_inventory();
        let create = tools
            .iter()
            .find(|tool| tool.name == "anki.note.create")
            .unwrap();
        let execute = tools
            .iter()
            .find(|tool| tool.name == "anki.execute")
            .unwrap();
        let status = tools
            .iter()
            .find(|tool| tool.name == "anki.webengine.status")
            .unwrap();

        assert_eq!(create.annotations["readOnlyHint"], false);
        assert_eq!(create.annotations["destructiveHint"], false);
        assert_eq!(execute.annotations["destructiveHint"], true);
        assert_eq!(status.annotations["readOnlyHint"], true);
        assert_eq!(status.annotations["idempotentHint"], true);
        assert!(create.input_schema["properties"].get("approved").is_none());
        assert!(execute.input_schema["properties"].get("approved").is_none());
        assert!(is_anki_mcp_tool("anki.app.get_state"));
        assert!(is_anki_mcp_tool("anki.execute"));
        assert!(!is_anki_mcp_tool("other.exec.run"));
    }

    #[test]
    fn rmcp_tool_models_preserve_inventory_fields() {
        let tools = mcp_tool_inventory();
        let models = tools.iter().map(mcp_tool_model).collect::<Vec<_>>();
        let names = models
            .iter()
            .map(|tool| tool.name.as_ref())
            .collect::<std::collections::BTreeSet<_>>();

        assert!(models.len() > 20);
        assert!(names.contains("anki.app.get_state"));
        assert!(!names.contains("anki.context.get_current"));
        assert!(names.contains("anki.note.search"));
        assert!(names.contains("anki.execute"));

        let search = models
            .iter()
            .find(|tool| tool.name.as_ref() == "anki.note.search")
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
    async fn rmcp_mutation_routes_directly_to_bridge_without_approval_preview() {
        let error = call_mcp_tool(
            BridgeHub::default(),
            "anki.note.create".to_string(),
            json!({"deck":"Deckhand Smoke","model":"Basic","fields":{"Front":"rmcp","Back":"preview"}}),
        )
        .await
        .unwrap_err()
        .to_string();

        assert!(!error.is_empty());
        assert!(!error.contains("requiresApproval"));
        assert!(!error.contains("elicitation"));
    }
}
