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

    # Each proxy appends the address it received the request from, so only the
    # right-hand entries can be trusted; the left ones are whatever the client sent.
    # Walk right to left and return the first hop that is not a trusted proxy.
    # Repeated header lines form one list.
    hops = [
        hop.strip()
        for k, v in headers
        if k.lower() == b"x-forwarded-for"
        for hop in v.decode("latin1").split(",")
        if hop.strip()
    ]
    for hop in reversed(hops):
        if not is_trusted_client_ip(hop, policy):
            return hop
    return hops[0] if hops else client_ip

__all__ = ["is_trusted_client_ip", "resolve_client_ip"]
