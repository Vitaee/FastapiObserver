# Security Defaults and Presets

### Default protections

| Protection | Default | Why |
|---|---|---|
| Body logging | `OFF` | Avoid leaking request/response secrets |
| Sensitive key masking | `ON` | Protect fields like `password`, `token`, `secret` |
| Sensitive header masking | `ON` | Protect `authorization`, `cookie`, `x-api-key` |
| Query string in logged path | Excluded | Prevent accidental token leakage |
| Request ID trust boundary | Trusted CIDRs only | Prevent spoofed correlation IDs |

### What redaction covers

The default formatter and OTLP log handler sanitize structured mappings and
sequences by **field and header name**. Matching ignores case and treats
underscores as hyphens. For example, `{"token": "example-secret"}` is masked;
`{"message": "token=example-secret"}` keeps its text. Sensitive header rules
apply inside a mapping named `headers`. Values under other names remain visible.

URL user/password pairs such as `postgresql://user:password@db/app` are replaced
with `postgresql://***:***@db/app` in request exception summaries, JSON
`error.message` / `error.stacktrace`, and the library's OTLP exception attributes.
This is a specific credential pattern, not a general URL or secret scanner.
It does not remove query tokens, arbitrary API keys, email addresses, phone
numbers or card numbers from free text. Regular log messages, arbitrary string
values and exception text outside that URL pattern can still contain them.

The `pci` and `gdpr` presets extend the configured field names and masking rules.
They do not detect every occurrence of personal or payment data and do not
establish regulatory compliance. Keep body capture off unless you need it.

These protections cover the library's own output paths. Independently configured
handlers and exporters require their own sanitizer.

### Retain an approved organisation sanitizer

If your application already has a redaction filter, JSON formatter or shared
Python/TypeScript sanitization contract, retain it with
[`configure_logging=False`](logging.md#application-owned-logging). Request
diagnostics pass through those existing handlers and their filters, including
structured context and exception records. Keep the same test vectors in your
application's integration tests, checking messages, `event`, `user_context`,
rendered tracebacks and every intended output/export destination.

If you want the library's JSON schema in an application-owned handler,
`StructuredJsonFormatter` already accepts a `sanitize_payload` callback:

```python
from fastapiobserver import StructuredJsonFormatter
from fastapiobserver.security import sanitize_record
from my_app.redaction import redact_payload  # Your existing, tested implementation.

def sanitize_payload(payload, policy):
    return redact_payload(sanitize_record(payload, policy))

handler.setFormatter(StructuredJsonFormatter(sanitize_payload=sanitize_payload))
```

This callback replaces the formatter's sanitization step; composing it with
`sanitize_record` retains the built-in field rules too. It covers that formatter's
output, not other handlers or direct OTel exports. The
[runnable application example](https://github.com/Vitaee/FastapiObserver/blob/main/examples/existing_logging_app.py)
demonstrates a custom filter instead, including traceback sanitization. Its
illustrative patterns are not a replacement for an approved sanitizer.

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

Captured bodies are cut at `OBS_MAX_BODY_LENGTH` bytes, then parsed as JSON or URL-encoded forms before sensitive keys are checked. JSON escapes and percent-encoded form names are decoded, and nested JSON values are redacted with the same policy as other structured fields. Repeated form fields are preserved.

Malformed or cut-off JSON, invalid forms and unsupported body formats (including free text and binary data) are masked as a whole. A cut-off form value can still be redacted when its key is complete. JSON and forms are re-encoded for the log; application traffic is unchanged. Values under keys outside the redacted fields remain visible. In `drop` mode, sensitive body values continue to be masked.

### Body capture media-type guard

```python
policy = SecurityPolicy(
    log_request_body=True,
    body_capture_media_types=("application/json",),
)
```

---

