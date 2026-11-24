"""Encrypted credential store for instances without macOS Keychain, such as a Linux VPS.

Each secret is sealed with AES-256-GCM, with the record's identity as associated data, so a value
cannot be moved to another identity. The 256-bit key comes from, in order:

1. systemd credentials: $CREDENTIALS_DIRECTORY/memyselfai-vault-key (LoadCredential or
   LoadCredentialEncrypted, which can bind the key to the host's TPM or credential secret),
2. a file named by $MEMYSELFAI_VAULT_KEY_FILE,
3. vault.key in the data directory, created on first use with mode 0600. This protects the
   secrets only as well as the file's permissions do, and the app says so in its log.

See docs/specs/instances-and-sync.md.
"""

import base64
import json
import os
from pathlib import Path
import secrets
import threading

KEY_NAME = "memyselfai-vault-key"
FILE_NAME = "vault.json"
VERSION = 1


class VaultError(RuntimeError):
    pass


def _aesgcm():
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as exc:
        raise VaultError(
            "The encrypted credential store needs the Python 'cryptography' package "
            "(Debian and Ubuntu: apt install python3-cryptography)."
        ) from exc
    return AESGCM


def _parse_key(raw):
    text = raw.strip()
    try:
        key = bytes.fromhex(text.decode("ascii"))
    except (UnicodeDecodeError, ValueError):
        key = text
    if len(key) != 32:
        raise VaultError("The credential store key must be 32 bytes, or 64 hexadecimal characters.")
    return key


def load_key(root):
    """Return (key, source). Never logs or returns the key's location contents elsewhere."""
    directory = os.environ.get("CREDENTIALS_DIRECTORY")
    if directory and (Path(directory) / KEY_NAME).is_file():
        return _parse_key((Path(directory) / KEY_NAME).read_bytes()), "systemd credential"
    named = os.environ.get("MEMYSELFAI_VAULT_KEY_FILE")
    if named:
        return _parse_key(Path(named).read_bytes()), "key file"
    path = Path(root) / "vault.key"
    if not path.exists():
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write(secrets.token_hex(32))
    if path.stat().st_mode & 0o077:
        raise VaultError(f"{path} must be readable only by its owner (chmod 600).")
    return _parse_key(path.read_bytes()), "data directory key file"


class FileVault:
    """Same interface as the macOS Keychain adapter: access(operation, identity, secret)."""

    def __init__(self, root, key=None):
        self.path = Path(root) / FILE_NAME
        if key is None:
            key, self.source = load_key(root)
        else:
            self.source = "supplied"
        self.cipher = _aesgcm()(key)
        self.lock = threading.Lock()

    def _load(self):
        if not self.path.exists():
            return {}
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if data.get("version") != VERSION or not isinstance(data.get("records"), dict):
            raise VaultError("Unsupported credential store format.")
        return data["records"]

    def _save(self, records):
        temporary = self.path.with_name(self.path.name + ".tmp")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"version": VERSION, "records": records}, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.path)

    def access(self, operation, identity, secret=None):
        account = str(identity).encode()
        with self.lock:
            records = self._load()
            name = base64.urlsafe_b64encode(account).decode()
            if operation == "read":
                sealed = records.get(name)
                if sealed is None:
                    raise VaultError("No stored credential for this connection.")
                blob = base64.b64decode(sealed)
                try:
                    return self.cipher.decrypt(blob[:12], blob[12:], account).decode()
                except Exception as exc:
                    raise VaultError("The stored credential could not be decrypted with this key.") from exc
            if operation == "store":
                nonce = secrets.token_bytes(12)
                records[name] = base64.b64encode(nonce + self.cipher.encrypt(nonce, secret.encode(), account)).decode()
            elif operation == "delete":
                if records.pop(name, None) is None:
                    return None
            else:
                raise ValueError("Unknown credential operation.")
            self._save(records)
        return None
