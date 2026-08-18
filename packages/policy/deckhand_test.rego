package deckhand.authz_test

import data.deckhand.authz.allow
import rego.v1

test_read_requires_authenticated_channel if {
	not allow with input as {
		"action": {"risk_class": "read", "mutation": false},
		"subject": {"name": "bobby", "device": "mac", "channel": "direct"},
	}
}

test_read_allowed_from_tailnet if {
	allow with input as {
		"action": {"risk_class": "read", "mutation": false},
		"subject": {"name": "bobby", "device": "mac", "channel": "tailscale"},
	}
}

test_mutation_denied_by_default if {
	not allow with input as {
		"action": {"id": "test.resource.ensure_active", "risk_class": "reversible", "mutation": true, "confirmation": "policy"},
		"subject": {"name": "bobby", "device": "mac", "channel": "tailscale"},
		"runtime": {"mutations_enabled": false, "audit_writable": true},
		"target": {"id": "210", "protected": false},
		"confirmation": {"valid": true, "request_digest": "x"},
		"request": {"digest": "x"},
	}
		with data.inventory as {"operators": ["operator"], "managed_devices": ["device"], "allowed_targets": {"test.resource.ensure_active": ["example"]}}
}

test_decision_is_structured if {
	result := data.deckhand.authz.decision with input as {
		"action": {"risk_class": "read", "mutation": false, "confirmation": "none"},
		"subject": {"name": "bobby", "device": "mac", "channel": "tailscale"},
	}
	result == {"allow": true, "reason": "allowed", "required_confirmation": "none"}
}

# --- Mutation allow matrix (positive + negative) ---

_mutation_input(overrides) := object.union(
	{
		"action": {"id": "test.resource.ensure_active", "risk_class": "reversible", "mutation": true, "confirmation": "confirm"},
		"subject": {"name": "operator", "device": "device", "channel": "mgmt-mtls"},
		"runtime": {"mutations_enabled": true, "audit_writable": true},
		"target": {"id": "example", "protected": false},
		"confirmation": {"valid": true, "request_digest": "d"},
		"request": {"digest": "d", "phase": "execute"},
	},
	overrides,
)

_inventory := {"operators": ["operator"], "managed_devices": ["device"], "allowed_targets": {"test.resource.ensure_active": ["example"]}}

test_mutation_allowed_when_all_conditions_met if {
	allow with input as _mutation_input({}) with data.inventory as _inventory
}

test_mutation_allowed_at_plan_phase_without_confirmation if {
	allow with input as _mutation_input({
		"request": {"digest": "d", "phase": "plan"},
		"confirmation": {"valid": false, "request_digest": null},
	})
		with data.inventory as _inventory
}

test_mutation_denied_on_protected_target if {
	not allow with input as _mutation_input({"target": {"id": "example", "protected": true}}) with data.inventory as _inventory
}

test_mutation_denied_for_non_allowlisted_target if {
	not allow with input as _mutation_input({"target": {"id": "not-allowed", "protected": false}}) with data.inventory as _inventory
}

test_mutation_denied_for_unknown_subject if {
	not allow with input as _mutation_input({"subject": {"name": "stranger", "device": "device", "channel": "mgmt-mtls"}}) with data.inventory as _inventory
}

test_mutation_denied_for_unmanaged_device if {
	not allow with input as _mutation_input({"subject": {"name": "operator", "device": "rogue", "channel": "mgmt-mtls"}}) with data.inventory as _inventory
}

test_mutation_denied_without_valid_confirmation if {
	not allow with input as _mutation_input({"confirmation": {"valid": false, "request_digest": null}}) with data.inventory as _inventory
}

test_mutation_denied_when_confirmation_digest_mismatches if {
	not allow with input as _mutation_input({
		"confirmation": {"valid": true, "request_digest": "other"},
		"request": {"digest": "d", "phase": "execute"},
	})
		with data.inventory as _inventory
}

test_mutation_denied_when_audit_unwritable if {
	not allow with input as _mutation_input({"runtime": {"mutations_enabled": true, "audit_writable": false}}) with data.inventory as _inventory
}

test_read_allowed_from_mcp_channel if {
	allow with input as {
		"action": {"risk_class": "read", "mutation": false},
		"subject": {"name": "bobby", "device": "mac", "channel": "mcp"},
	}
}
