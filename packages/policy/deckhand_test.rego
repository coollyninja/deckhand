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
