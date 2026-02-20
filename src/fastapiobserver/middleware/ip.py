from __future__ import annotations

from starlette.types import Scope

from ..security import (
    TrustedProxyPolicy,
    is_trusted_client_ip,
    resolve_client_ip,
)


def _extract_scope_client_ip(scope: Scope) -> str | None:
    client = scope.get("client")
    if not client:
        return None
    if isinstance(client, (tuple, list)) and client:
        client_ip = client[0]
        if isinstance(client_ip, str):
            return client_ip
    return None

class _IpResolver:
    def __init__(self, policy: TrustedProxyPolicy) -> None:
        self.policy = policy

    def resolve(
        self,
        scope: Scope,
        headers: list[tuple[bytes, bytes]],
    ) -> tuple[str | None, bool]:
        scope_client_ip = _extract_scope_client_ip(scope)
        trusted_source = (
            is_trusted_client_ip(scope_client_ip, self.policy)
            if self.policy.enabled
            else True
        )
        client_ip = resolve_client_ip(scope_client_ip, headers, self.policy)
        return client_ip, trusted_source

__all__ = ["_IpResolver", "_extract_scope_client_ip"]
