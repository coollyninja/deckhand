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
		"action": {"id": "pve.vm.ensure_running", "risk_class": "reversible", "mutation": true, "confirmation": "policy"},
		"subject": {"name": "bobby", "device": "mac", "channel": "tailscale"},
		"runtime": {"mutations_enabled": false, "audit_writable": true},
		"target": {"id": "210", "protected": false},
		"confirmation": {"valid": true, "request_digest": "x"},
		"request": {"digest": "x"},
	}
		with data.inventory as {"operators": ["bobby"], "managed_devices": ["mac"], "allowed_targets": {"pve.vm.ensure_running": ["210"]}}
}
