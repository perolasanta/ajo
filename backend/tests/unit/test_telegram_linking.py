from __future__ import annotations

import hashlib
import re
import unittest
import uuid

from app.domains.channels.telegram import decode_token_tenant, make_link_token, token_hash

try:
    import pytest
except ModuleNotFoundError:
    pytest = None
if pytest is not None:
    pytestmark = pytest.mark.unit


class TelegramTokenTests(unittest.TestCase):
    def test_token_encodes_tenant_without_unsafe_characters_and_hashes_only_raw_token(self) -> None:
        cooperative_id = uuid.uuid4()
        token = make_link_token(cooperative_id)
        self.assertLessEqual(len(token), 64)
        self.assertRegex(token, re.compile(r"\A[A-Za-z0-9_-]+\Z"))
        self.assertEqual(decode_token_tenant(token), cooperative_id)
        self.assertEqual(token_hash(token), hashlib.sha256(token.encode()).digest())

    def test_tenant_decoder_rejects_malformed_tokens(self) -> None:
        for candidate in ("", "../../bad", "x", "a" * 65, "e" * 22 + "!" + "b" * 32):
            with self.subTest(candidate=candidate):
                self.assertIsNone(decode_token_tenant(candidate))
