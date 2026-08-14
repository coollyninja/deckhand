package deckhand.authz

import rego.v1

default allow := false

decision := {
	"allow": allow,
	"reason": reason,
	"required_confirmation": required_confirmation,
}

default reason := "request denied by policy"

reason := "allowed" if allow

default required_confirmation := "none"

required_confirmation := input.action.confirmation if {
	input.action.mutation == true
}

allow if {
	input.action.risk_class == "read"
	input.subject.name != ""
	input.subject.device != ""
	input.subject.channel in {"tailscale", "mgmt-mtls"}
}

allow if {
	input.action.mutation == true
	input.runtime.mutations_enabled == true
	input.runtime.audit_writable == true
	input.subject.name in data.inventory.operators
	input.subject.device in data.inventory.managed_devices
	input.subject.channel in {"tailscale", "mgmt-mtls"}
	input.target.id in data.inventory.allowed_targets[input.action.id]
	not input.target.protected
	input.request.phase == "plan"
}

allow if {
	input.action.mutation == true
	input.runtime.mutations_enabled == true
	input.runtime.audit_writable == true
	input.subject.name in data.inventory.operators
	input.subject.device in data.inventory.managed_devices
	input.subject.channel in {"tailscale", "mgmt-mtls"}
	input.target.id in data.inventory.allowed_targets[input.action.id]
	not input.target.protected
	input.request.phase == "execute"
	confirmation_satisfied
}

confirmation_satisfied if {
	input.action.confirmation == "none"
}

confirmation_satisfied if {
	input.action.confirmation != "none"
	input.confirmation.valid == true
	input.confirmation.request_digest == input.request.digest
}
