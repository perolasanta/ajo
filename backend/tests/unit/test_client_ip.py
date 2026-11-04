"""Trusted-proxy client IP resolution tests."""

from __future__ import annotations

import unittest

try:
    import pytest
except ModuleNotFoundError:  # Permit the stdlib runner in minimal local environments.
    pytest = None

if pytest is not None:
    pytestmark = pytest.mark.unit

from app.core.client_ip import resolve_client_ip


class ClientIpTests(unittest.TestCase):
    def test_untrusted_peer_cannot_spoof_forwarded_headers(self) -> None:
        self.assertEqual(
            resolve_client_ip(
                peer="198.51.100.10",
                forwarded_for="203.0.113.99",
                real_ip="203.0.113.99",
                trusted_proxies="",
            ),
            "198.51.100.10",
        )

    def test_trusted_proxy_forwarded_for_is_honored(self) -> None:
        self.assertEqual(
            resolve_client_ip(
                peer="10.0.0.2",
                forwarded_for="203.0.113.9",
                real_ip=None,
                trusted_proxies="10.0.0.0/8",
            ),
            "203.0.113.9",
        )

    def test_only_nearest_untrusted_forwarded_hop_is_used(self) -> None:
        self.assertEqual(
            resolve_client_ip(
                peer="10.0.0.2",
                forwarded_for="198.51.100.99, 203.0.113.9",
                real_ip=None,
                trusted_proxies="10.0.0.0/8",
            ),
            "203.0.113.9",
        )

    def test_trusted_proxy_real_ip_is_fallback_when_forwarded_for_absent(self) -> None:
        self.assertEqual(
            resolve_client_ip(
                peer="10.0.0.2",
                forwarded_for=None,
                real_ip="203.0.113.9",
                trusted_proxies="10.0.0.0/8",
            ),
            "203.0.113.9",
        )
