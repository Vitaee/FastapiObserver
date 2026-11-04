# Draft operation contract

The [draft specification](https://github.com/Vitaee/FastapiObserver/tree/main/spec)
records the implemented experimental operation/call behavior. Its JSON event
representation remains schema **0.1.0**, independently of Python package/API
versions. It is incomplete and has no stable or cross-language conformance claim.

The [JSON Schema](https://github.com/Vitaee/FastapiObserver/blob/main/spec/operation-event.schema.json)
and [positive/negative vectors](https://github.com/Vitaee/FastapiObserver/blob/main/spec/fixtures/operation-events.json)
ship in the source archive. Tests validate these structures and real emitted
events; runtime tests separately cover ownership, closure, concurrency,
deduplication and resource limits. JSON Schema alone cannot prove those behaviors.

Call totals belong to the operation where recording began. Finishing on another
thread or in a nested operation does not transfer ownership. Closed/collected
owners and inherited parent-process handles cannot accept completion. Native
SQL successful-execution counts and HTTP native/span deduplication retain their
existing meaning; sampled fallback remains partial and bounded.

Unknown dependency groups are not measured zero. Manual adapters must select
their observation layer and avoid recording the same call through another
native adapter or third-party span fallback. This API is not an automatic
Redis, MongoDB or Tortoise integration.

See [manual recording](experimental-core.md#custom-clients-and-callbacks) and
[the ownership decision](decisions/0017-record-calls-against-their-start-owner.md).
