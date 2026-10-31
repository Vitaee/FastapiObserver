# Security policy

## Supported versions

Security fixes go into the latest minor release. Upgrade to the newest `fastapi-observer` on PyPI to receive them.

| Version | Supported |
|---|---|
| 1.5.x | Yes |
| Older | No |

## Reporting a vulnerability

Please report vulnerabilities privately through GitHub: open the repository's **Security** tab and choose **Report a vulnerability**. Do not open a public issue.

Include the affected version, your configuration (for example which `SecurityPolicy` preset and extras you use), and steps or a small app that reproduces the problem.

You can expect an acknowledgement within a few days. Confirmed issues are fixed in a new release, and the fix is listed under **Security** in [CHANGELOG.md](CHANGELOG.md).

## Scope

Reports are especially welcome for:

- data that bypasses redaction (`SecurityPolicy`, presets, allowlists);
- trust-boundary issues, such as forged request IDs, `traceparent` headers or client IPs;
- unauthorised access to the runtime control endpoint.
