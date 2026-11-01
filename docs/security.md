# Security Defaults and Presets

### Default protections

| Protection | Default | Why |
|---|---|---|
| Body logging | `OFF` | Avoid leaking request/response secrets |
| Sensitive key masking | `ON` | Protect fields like `password`, `token`, `secret` |
| Sensitive header masking | `ON` | Protect `authorization`, `cookie`, `x-api-key` |
| Query string in logged path | Excluded | Prevent accidental token leakage |
| Request ID trust boundary | Trusted CIDRs only | Prevent spoofed correlation IDs |

### Presets for regulated environments

```python
from fastapiobserver import SecurityPolicy

# Strictest option: drop sensitive values and keep minimal safe headers
strict_policy = SecurityPolicy.from_preset("strict")

# PCI-focused redaction fields
pci_policy = SecurityPolicy.from_preset("pci")

# GDPR-focused hashed PII fields
gdpr_policy = SecurityPolicy.from_preset("gdpr")
```

The `hash` mode, used by `gdpr`, writes `hmac-sha256:<hex>`: an HMAC keyed with `OBS_REDACTION_HASH_KEY` (or `SecurityPolicy(hash_key=...)`). Keep the key secret. Without one, a random key is picked at startup, so hashes differ between processes and after a restart, and a warning says so.

Use a preset in installation:

```python
install_observability(app, settings, security_policy=SecurityPolicy.from_preset("pci"))
```

### Allowlist-only logging (audit-style)

If your compliance model is "log only approved fields", use allowlists:

```python
from fastapiobserver import SecurityPolicy

policy = SecurityPolicy(
    header_allowlist=("x-request-id", "content-type", "user-agent"),
    event_key_allowlist=("method", "path", "status_code"),
)
```

### Body capture

Captured bodies are cut at `OBS_MAX_BODY_LENGTH` bytes, then the values of sensitive keys are redacted in JSON (`"password": "..."`) and form (`password=...`) bodies, including a value the cut left unfinished. Bodies are matched as text, not parsed, so a secret under a key that isn't in the redacted fields, or in free text, is written as it is. In `drop` mode, values in bodies are masked, since a cut-off pair can't be removed cleanly.

### Body capture media-type guard

```python
policy = SecurityPolicy(
    log_request_body=True,
    body_capture_media_types=("application/json",),
)
```

---

