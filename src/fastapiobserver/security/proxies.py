import functools
import ipaddress

from .policy import TrustedProxyPolicy


@functools.lru_cache(maxsize=1024)
def is_trusted_client_ip(client_ip: str | None, policy: TrustedProxyPolicy) -> bool:
    if not policy.enabled:
        return True
    if not client_ip:
        return False
    try:
        parsed_ip = ipaddress.ip_address(client_ip)
    except ValueError:
        return False

    for cidr in policy.trusted_cidrs:
        try:
            network = ipaddress.ip_network(cidr, strict=False)
        except ValueError:
            continue
        if parsed_ip in network:
            return True
    return False


def resolve_client_ip(
    client_ip: str | None,
    headers: list[tuple[bytes, bytes]],
    policy: TrustedProxyPolicy,
) -> str | None:
    if not policy.honor_forwarded_headers:
        return client_ip
    if not is_trusted_client_ip(client_ip, policy):
        return client_ip

    forwarded_for = None
    for k, v in headers:
        if k.lower() == b"x-forwarded-for":
            forwarded_for = v.decode("latin1")
            break

    if not forwarded_for:
        return client_ip
    first_hop = forwarded_for.split(",")[0].strip()
    return first_hop or client_ip

__all__ = ["is_trusted_client_ip", "resolve_client_ip"]
