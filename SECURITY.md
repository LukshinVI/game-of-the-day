# Security Policy

## Reporting a vulnerability

Please do not disclose security vulnerabilities, credentials, tokens, private infrastructure identifiers, or personal data in a public issue.

If you discover exposed sensitive information, contact the repository owner privately through GitHub before publishing details.

## Repository hygiene

This repository is intended to contain source code, architecture documentation, tests, and sanitized examples only.

The following must never be committed:

- API keys, access tokens, passwords, private keys, cookies, or authorization headers;
- `.env` files or local secret stores;
- production databases, exports, backups, logs, or raw user feedback;
- real recipient/user/channel identifiers;
- private IP addresses, production origin addresses, provider hostnames, tunnel/connector identifiers, or machine usernames;
- personal names, email addresses, or other identifying user data unless intentionally public;
- credentials or identifiers copied from terminal output, screenshots, support messages, or deployment logs.

Documentation and examples should use placeholders such as `<VPS_HOST>`, `<API_PORT>`, `<RECIPIENT_ID>`, `<TUNNEL_ID>`, and `<SECRET>`.

Secrets used by runtime components must be supplied through the deployment platform or environment and kept outside Git.

## Before making the repository public

Public release requires a review of both the current tree and Git history. Removing a secret from the latest revision is not sufficient if it exists in an earlier commit. Any credential that has ever been committed must be treated as compromised and rotated, even after history is rewritten.
