# Security policy

Deckhand is a security-sensitive control plane. Please report suspected vulnerabilities privately through GitHub's private vulnerability reporting for `coollyninja/deckhand`. Do not open a public issue containing credentials, internal topology, or exploit details.

Production deployments must bind the broker to loopback behind an authenticated proxy, use deny-by-default policy, keep mutation credentials purpose-scoped, and retain an independent recovery path. Development identity mode is not a production authentication mechanism.

