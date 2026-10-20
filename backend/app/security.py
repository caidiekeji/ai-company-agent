"""凭证加密：系统密钥链优先（keyring），回退本地加密文件（Fernet）。不明文落 SQLite。"""
import base64
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from .db import DATA_DIR

SERVICE = "ai-company-agent"
KEY_FILE = DATA_DIR / ".master.key"
_keyring_ok = True
try:
    import keyring  # noqa: F401
except Exception:  # pragma: no cover
    _keyring_ok = False


def _fernet() -> Fernet:
    if not KEY_FILE.exists():
        KEY_FILE.write_bytes(base64.urlsafe_b64encode(os.urandom(32)))
        try:
            os.chmod(KEY_FILE, 0o600)
        except OSError:
            pass
    return Fernet(KEY_FILE.read_bytes())


def store_secret(provider: str, alias: str, api_key: str) -> str:
    """保存密钥，返回引用（供 SQLite 存储）。"""
    ref = f"keyring::{provider}::{alias}"
    if _keyring_ok:
        try:
            import keyring

            keyring.set_password(SERVICE, ref, api_key)
            return ref
        except Exception:
            pass
    ref = f"file::{provider}::{alias}"
    path = DATA_DIR / "creds.enc"
    lines: list[str] = []
    if path.exists():
        try:
            lines = _fernet().decrypt(path.read_bytes()).decode().splitlines()
        except InvalidToken:
            lines = []
    lines = [ln for ln in lines if not ln.startswith(f"{ref}\t")]
    lines.append(f"{ref}\t{api_key.decode() if isinstance(api_key, bytes) else api_key}")
    path.write_bytes(_fernet().encrypt("\n".join(lines).encode()))
    return ref


def load_secret(ref: str) -> str | None:
    if ref.startswith("keyring::"):
        if _keyring_ok:
            try:
                import keyring

                return keyring.get_password(SERVICE, ref)
            except Exception:
                return None
        return None
    if ref.startswith("file::"):
        path = DATA_DIR / "creds.enc"
        if not path.exists():
            return None
        try:
            for ln in _fernet().decrypt(path.read_bytes()).decode().splitlines():
                k, _, v = ln.partition("\t")
                if k == ref:
                    return v
        except InvalidToken:
            return None
    return None


def delete_secret(ref: str) -> None:
    if ref.startswith("keyring::") and _keyring_ok:
        try:
            import keyring

            keyring.delete_password(SERVICE, ref)
            return
        except Exception:
            pass
    if ref.startswith("file::"):
        path = DATA_DIR / "creds.enc"
        if not path.exists():
            return
        try:
            lines = [
                ln
                for ln in _fernet().decrypt(path.read_bytes()).decode().splitlines()
                if not ln.startswith(ref + "\t")
            ]
            path.write_bytes(_fernet().encrypt("\n".join(lines).encode()))
        except InvalidToken:
            pass


def mask_secret(ref: str) -> str:
    val = load_secret(ref)
    if not val:
        return "•••（未找到）"
    if len(val) <= 8:
        return "••••"
    return val[:4] + "••••" + val[-4:]
