# OIDC login

NetAtlas can sign people in through an OpenID Connect provider such as Authentik, Keycloak, Entra ID or Google. Local accounts keep working, so an Admin can always log in with a password even if the provider is down.

## Settings

Set these in `.env`, then run `docker compose up -d`. NetAtlas refuses to start if they are incomplete or unsafe.

| Variable | Required | Meaning |
| --- | --- | --- |
| `NETATLAS_OIDC_ISSUER` | Yes | The provider's issuer URL. It must use `https://`. |
| `NETATLAS_OIDC_CLIENT_ID` | Yes | The client ID from the provider. |
| `NETATLAS_OIDC_CLIENT_SECRET` | Yes | The client secret from the provider. |
| `NETATLAS_OIDC_PROVIDER_NAME` | No | The name on the login button. Defaults to `OIDC`. |
| `NETATLAS_OIDC_DEFAULT_ROLE` | No | The Role of a new user: `viewer` (default) or `editor`. |
| `NETATLAS_OIDC_ALLOWED_EMAIL_DOMAINS` | No | Comma-separated domains. Only verified addresses in them can log in. |
| `NETATLAS_OIDC_REDIRECT_URI` | No | The full callback URL. Set it if NetAtlas runs behind a reverse proxy. |

In the provider, register this redirect URI:

```text
https://netatlas.example.com/login/oidc/callback/
```

## Who can log in

**Everyone the provider lets through this client can log in and read all of NetAtlas.** Restrict access in the provider, or set `NETATLAS_OIDC_ALLOWED_EMAIL_DOMAINS`.

- Google accounts are public, so NetAtlas requires the domain list for Google.
- For Entra ID, use your tenant's issuer, `https://login.microsoftonline.com/<tenant-id>/v2.0`. The shared `common` and `organizations` endpoints are refused.
- The domain list needs the provider to send `email_verified`. Entra ID does not send it by default, so restrict Entra ID through its app assignment instead.

## How accounts work

- The first login creates a user with the default Role. An Admin can change it under **Users**, and later logins keep it. Admin is never given automatically.
- A provider account is identified by its issuer and subject. It is never matched to an existing user by name or email. If the name is taken, the new user gets a suffix, such as `anna-2`.
- A user created this way has no password and logs in only through the provider.
- Changing `NETATLAS_OIDC_ISSUER` means the same people get new users, because the issuer is part of their identity.
- Logging out ends the NetAtlas session only, not the session at the provider.

## Example: Authentik

1. In Authentik, open **Applications → Providers → Create** and choose **OAuth2/OpenID Provider**.
2. Set **Client type** to **Confidential**, and add the redirect URI above as a **strict** match.
3. Keep the default scopes `openid`, `email` and `profile`, and choose an RS256 **Signing Key**.
4. Create an **Application** named `NetAtlas` that uses this provider, and bind the groups that may log in.
5. Copy the **Client ID**, **Client Secret** and **OpenID Configuration Issuer** into `.env`:

```sh
NETATLAS_OIDC_ISSUER=https://auth.example.com/application/o/netatlas/
NETATLAS_OIDC_CLIENT_ID=your-client-id
NETATLAS_OIDC_CLIENT_SECRET=your-client-secret
NETATLAS_OIDC_PROVIDER_NAME=Authentik
```

6. Run `docker compose up -d`. The login page now shows **Log in with Authentik**.
