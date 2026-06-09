use serde_json::Value;

#[cfg(test)]
pub fn contract_fixture() -> Value {
    serde_json::from_str(include_str!("../fixtures/anki-access-contract.v1.json"))
        .expect("contract fixture is valid JSON")
}

#[cfg(test)]
pub fn tool_call_fixture() -> Value {
    serde_json::from_str(include_str!("../fixtures/anki-tool-call.fixture.json"))
        .expect("tool call fixture is valid JSON")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn contract_has_anki_bridge_transport() {
        let contract = contract_fixture();
        assert_eq!(
            contract["transport"]["ankiBridge"]["direction"],
            "anki_addon_connects_outbound"
        );
        assert!(contract["transport"].get("directExecutor").is_none());
        assert!(!contract["toolNamespaces"]
            .as_array()
            .expect("tool namespaces are an array")
            .iter()
            .any(|namespace| namespace == "anki.dev"));
        assert!(!contract["toolNamespaces"]
            .as_array()
            .expect("tool namespaces are an array")
            .iter()
            .any(|namespace| namespace == "anki.navigate"));
        assert_eq!(contract["registration"]["method"], "anki.bridge.hello");
        assert_eq!(contract["callEnvelope"]["method"], "tool.call");
        assert!(contract["callEnvelope"]["params"].get("approval").is_none());
    }

    #[test]
    fn tool_call_fixture_uses_anki_namespace() {
        let fixture = tool_call_fixture();
        assert_eq!(fixture["method"], "tool.call");
        assert_eq!(fixture["params"]["tool"], "anki.context.get_current");
        assert!(fixture["params"].get("approval").is_none());
    }
}
