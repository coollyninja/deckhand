// Fetches a short-lived signed identity token from a trusted LOCAL issuer, which
// the broker verifies. The plugin never holds a signing key; it asks the local
// issuer (a loopback service on the Mac) for a token per request window. Tokens
// are cached until shortly before expiry to avoid a round-trip on every call.

export class IdentityError extends Error {}

type TokenResponse = { token: string; expires_at: string };

const DEFAULT_ISSUER = "http://127.0.0.1:19472/token";

export class IdentityProvider {
  private cached: { token: string; expiresAt: number } | null = null;

  constructor(private readonly issuerUrl: string = DEFAULT_ISSUER) {}

  async token(): Promise<string> {
    const now = Date.now();
    // Reuse a cached token until 10s before it expires.
    if (this.cached && this.cached.expiresAt - 10_000 > now) {
      return this.cached.token;
    }
    let response: Response;
    try {
      response = await fetch(this.issuerUrl, { signal: AbortSignal.timeout(3_000) });
    } catch (cause) {
      throw new IdentityError(`identity issuer unreachable: ${String(cause)}`);
    }
    if (!response.ok) {
      throw new IdentityError(`identity issuer returned ${response.status}`);
    }
    const body = (await response.json()) as TokenResponse;
    if (!body.token) throw new IdentityError("identity issuer returned no token");
    this.cached = { token: body.token, expiresAt: Date.parse(body.expires_at) || now + 30_000 };
    return body.token;
  }
}
