"""Resolve a client address while trusting forwarding headers only from known proxies."""

from __future__ import annotations

from ipaddress import IPv4Address, IPv4Network, IPv6Address, IPv6Network, ip_address, ip_network

IPAddress = IPv4Address | IPv6Address
IPNetwork = IPv4Network | IPv6Network


def _is_trusted(address: IPAddress, networks: tuple[IPNetwork, ...]) -> bool:
    for network in networks:
        try:
            if address in network:
                return True
        except TypeError:
            continue
    return False


def _trusted_networks(value: str) -> tuple[IPNetwork, ...]:
    networks: list[IPNetwork] = []
    for item in value.split(","):
        candidate = item.strip()
        if not candidate:
            continue
        try:
            networks.append(ip_network(candidate, strict=False))
        except ValueError:
            # A malformed trust entry must never expand the trusted set.
            continue
    return tuple(networks)


def resolve_client_ip(
    *,
    peer: str | None,
    forwarded_for: str | None,
    real_ip: str | None,
    trusted_proxies: str,
) -> str | None:
    """Return the socket peer unless that peer is trusted to assert client addresses.

    X-Forwarded-For is walked right-to-left, discarding trusted proxy hops. The
    first untrusted address is the client; any malformed chain falls back to the
    socket peer. X-Real-IP is a fallback only when X-Forwarded-For is absent.
    """
    if peer is None:
        return None
    try:
        peer_address = ip_address(peer.strip())
    except ValueError:
        return peer

    networks = _trusted_networks(trusted_proxies)
    if not _is_trusted(peer_address, networks):
        return str(peer_address)

    if forwarded_for:
        try:
            chain = tuple(ip_address(part.strip()) for part in forwarded_for.split(","))
            if not chain:
                return str(peer_address)
        except ValueError:
            return str(peer_address)

        current = peer_address
        for forwarded_address in reversed(chain):
            if not _is_trusted(current, networks):
                break
            current = forwarded_address
        return str(current)

    if real_ip:
        try:
            return str(ip_address(real_ip.strip()))
        except ValueError:
            return str(peer_address)
    return str(peer_address)
