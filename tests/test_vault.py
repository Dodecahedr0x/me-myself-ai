"""The encrypted credential store used where macOS Keychain is unavailable."""

import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

try:
    import cryptography  # noqa: F401
except ImportError:
    cryptography = None

import connectors
import vault


@unittest.skipIf(cryptography is None, "Needs the cryptography package, as on the Linux service.")
class VaultTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        env = patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        for name in ("CREDENTIALS_DIRECTORY", "MEMYSELFAI_VAULT_KEY_FILE"):
            os.environ.pop(name, None)

    def test_round_trip_is_encrypted_at_rest_and_private(self):
        store = vault.FileVault(self.root)
        self.assertEqual(store.source, "data directory key file")
        store.access("store", "conn-1", "xoxp-fixture-token")
        raw = (self.root / "vault.json").read_bytes()
        self.assertNotIn(b"xoxp-fixture-token", raw)
        self.assertEqual(stat.S_IMODE((self.root / "vault.json").stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE((self.root / "vault.key").stat().st_mode), 0o600)
        self.assertEqual(vault.FileVault(self.root).access("read", "conn-1"), "xoxp-fixture-token")
        store.access("store", "conn-1", "rotated")
        self.assertEqual(store.access("read", "conn-1"), "rotated")
        store.access("delete", "conn-1")
        store.access("delete", "conn-1")
        with self.assertRaises(vault.VaultError):
            store.access("read", "conn-1")

    def test_wrong_key_or_moved_record_cannot_be_read(self):
        store = vault.FileVault(self.root)
        store.access("store", "a", "secret-a")
        other = vault.FileVault(self.root, key=os.urandom(32))
        with self.assertRaises(vault.VaultError):
            other.access("read", "a")
        records = store._load()
        name_a = next(iter(records))
        store._save({**records, "Yg==": records[name_a]})  # "b", with a's ciphertext
        with self.assertRaises(vault.VaultError):
            store.access("read", "b")

    def test_key_sources(self):
        credentials = self.root / "creds"
        credentials.mkdir()
        (credentials / vault.KEY_NAME).write_text(os.urandom(32).hex())
        with patch.dict(os.environ, {"CREDENTIALS_DIRECTORY": str(credentials)}):
            self.assertEqual(vault.FileVault(self.root).source, "systemd credential")
        key_file = self.root / "key"
        key_file.write_text("too short")
        with patch.dict(os.environ, {"MEMYSELFAI_VAULT_KEY_FILE": str(key_file)}):
            with self.assertRaises(vault.VaultError):
                vault.FileVault(self.root)
        loose = self.root / "loose"
        loose.mkdir()
        (loose / "vault.key").write_text(os.urandom(32).hex())
        (loose / "vault.key").chmod(0o644)
        with self.assertRaises(vault.VaultError):
            vault.FileVault(loose)

    def test_connectors_use_the_vault_once_configured(self):
        with patch.dict(os.environ, {"MEMYSELFAI_FILE_VAULT": "1"}), patch.object(connectors, "_vault", None):
            self.assertEqual(connectors.configure_secrets(self.root), "data directory key file")
            connectors.store_secret("conn", "token-value")
            self.assertEqual(connectors._secret("conn"), "token-value")
            connectors.delete_secret("conn")
            self.assertNotIn(b"token-value", (self.root / "vault.json").read_bytes())


if __name__ == "__main__":
    unittest.main()
