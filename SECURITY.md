# Security Policy

## Reporting a vulnerability

Please do not publish credentials, tokens, private infrastructure details, personal data, or exploitable security findings in a public issue.

For a suspected vulnerability, use a private reporting channel provided by the repository host when available. Include enough information to reproduce and assess the issue without including unrelated personal or production data.

## Repository security rules

The repository must not contain:

- API tokens, passwords, private keys, session credentials, or secret-derived values;
- production hostnames, origin IP addresses, tunnel/account identifiers, or runtime usernames when they are not part of a deliberate public interface;
- production database files, exports, backups, or raw user feedback;
- recipient identities or private relationship details;
- environment-specific internal filesystem paths or internal ports unless intentionally documented as part of a public contract;
- screenshots or logs containing any of the above.

Public documentation should use role-based terms such as `recipient`, `caregiver`, `child profile`, `application host`, and `production origin`, together with placeholders such as `api.<project-domain>`.

## Secret handling

Runtime secrets belong in the deployment environment or the relevant platform secret store. Example environment files must contain placeholders only.

A secret that is accidentally committed must be considered compromised even if the commit is later removed. Rotate/revoke it first, then remove the exposed material from the repository and history as appropriate.

## Application security boundaries

The architecture is designed around the following principles:

- validate external callbacks before business processing;
- authenticate persistence endpoints;
- expose only required public routes;
- keep durable storage off the public network;
- isolate test and production state;
- validate structured AI output before it can affect durable state;
- keep workflow ownership and external side effects in deterministic application code;
- avoid logging credentials, secret headers, or unnecessary raw user content.

## AI-specific safety boundary

LLM output is treated as untrusted structured input. Models do not own authentication, authorization, persistence, workflow state, idempotency, retries, or external side effects. Application code validates model output before it is accepted.

## Supported versions

The actively maintained default branch is the supported development line. Historical revisions and experimental branches may not receive security fixes.
