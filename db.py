from __future__ import annotations

import calendar
import base64
import json
import os
import shutil
import sqlite3
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

import pandas as pd


def _config_value(key: str, default: str = "") -> str:
    env = (os.getenv(key, "") or "").strip()
    if env:
        return env
    try:
        import streamlit as st  # local import to avoid hard dependency at module import time

        if key in st.secrets:
            raw = st.secrets[key]
        else:
            raw = st.secrets.get(key, default)
    except Exception:
        raw = default
    if raw is None:
        return str(default or "").strip()
    return str(raw).strip()


def _config_bool(key: str, default: bool = False) -> bool:
    raw = _config_value(key, "1" if default else "0").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _config_int(key: str, default: int) -> int:
    raw = _config_value(key, str(default))
    try:
        return int(float(raw))
    except Exception:
        return int(default)


def _resolve_db_path() -> Path:
    # Priority:
    # 1) HR_DB_PATH (explicit db file path)
    # 2) HR_DATA_DIR/finance_hub.db (persistent data directory)
    # 3) OS user data directory (survives app file replace/re-upload)
    # 4) local default beside this file (legacy fallback/migration source)
    legacy_local = Path(__file__).with_name("finance_hub.db")

    def _default_data_dir() -> Path:
        home = Path.home()
        if sys.platform == "darwin":
            return home / "Library" / "Application Support" / "HeadlineRentalsStaffApp"
        if os.name == "nt":
            appdata = (os.getenv("APPDATA", "") or "").strip()
            base = Path(appdata).expanduser() if appdata else home
            return base / "HeadlineRentalsStaffApp"
        return home / ".headline_rentals_staff_app"

    explicit = (os.getenv("HR_DB_PATH", "") or "").strip()
    if explicit:
        db_path = Path(explicit).expanduser()
    else:
        data_dir = (os.getenv("HR_DATA_DIR", "") or "").strip()
        if data_dir:
            db_path = Path(data_dir).expanduser() / "finance_hub.db"
        else:
            db_path = _default_data_dir() / "finance_hub.db"

    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        # Sandbox or host permissions can block user-data folders.
        # Fall back to local project path so app startup never fails.
        db_path = legacy_local
        db_path.parent.mkdir(parents=True, exist_ok=True)
    migrate_legacy = str(os.getenv("HR_MIGRATE_LEGACY_DB", "0")).strip().lower() in {
        "1",
        "true",
        "yes",
    }
    if migrate_legacy and db_path != legacy_local and not db_path.exists() and legacy_local.exists():
        # Optional one-time migration from legacy local DB.
        # Disabled by default to avoid reviving stale/deleted historical data on fresh deploys.
        try:
            shutil.copy2(legacy_local, db_path)
        except Exception:
            pass
    return db_path


DB_PATH = _resolve_db_path()
AUTO_BACKUP_DIR = DB_PATH.parent / "auto_backups"
AUTO_BACKUP_MIN_SECONDS = max(
    15,
    _config_int("HR_AUTO_BACKUP_MIN_SECONDS", 30),
)
AUTO_BACKUP_HARD_CAP = 10
AUTO_BACKUP_KEEP = max(
    1,
    min(AUTO_BACKUP_HARD_CAP, _config_int("HR_AUTO_BACKUP_KEEP", AUTO_BACKUP_HARD_CAP)),
)
AUTO_BACKUP_INACTIVITY_SECONDS = max(
    3600,
    _config_int("HR_AUTO_BACKUP_INACTIVITY_SECONDS", 2 * 24 * 60 * 60),
)


def _resolve_db_mirror_paths() -> list[Path]:
    """
    Optional DB mirrors.
    Mirrors help keep a copy in user-visible locations (e.g. project/Documents)
    even when the runtime DB lives in OS app-data paths.
    """
    configured = _config_value("HR_DB_MIRROR_PATHS", "")
    candidates: list[Path] = []
    if configured:
        for raw in re.split(r"[,\n;]+", configured):
            token = str(raw or "").strip()
            if token:
                candidates.append(Path(token).expanduser())

    legacy_local = Path(__file__).with_name("finance_hub.db")
    legacy_local_live = Path(__file__).with_name("finance_hub_live_backup.db")
    documents_workspace = Path.home() / "Documents" / "New project" / "finance_hub.db"
    documents_workspace_live = (
        Path.home() / "Documents" / "New project" / "finance_hub_live_backup.db"
    )
    candidates.extend(
        [
            legacy_local,
            legacy_local_live,
            documents_workspace,
            documents_workspace_live,
        ]
    )

    mirrors: list[Path] = []
    seen: set[str] = set()
    try:
        db_resolved = str(DB_PATH.expanduser().resolve())
    except Exception:
        db_resolved = str(DB_PATH.expanduser())
    for candidate in candidates:
        try:
            resolved = str(candidate.expanduser().resolve())
        except Exception:
            resolved = str(candidate.expanduser())
        if resolved == db_resolved:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        mirrors.append(candidate)
    return mirrors


DB_MIRROR_PATHS = _resolve_db_mirror_paths()
AUTO_RESTORE_SUPPRESS_MARKER = DB_PATH.parent / ".suppress_auto_restore"
REMOTE_BACKUP_REPO = _config_value("HR_REMOTE_BACKUP_REPO", "")
REMOTE_BACKUP_TOKEN = _config_value("HR_REMOTE_BACKUP_TOKEN", "")
REMOTE_BACKUP_BRANCH = _config_value("HR_REMOTE_BACKUP_BRANCH", "backup-storage") or "backup-storage"
REMOTE_BACKUP_FILE = (
    _config_value("HR_REMOTE_BACKUP_FILE", "backups/finance_hub_live_backup.db")
    or "backups/finance_hub_live_backup.db"
).strip()
REMOTE_BACKUP_MIN_SECONDS = max(
    30,
    _config_int("HR_REMOTE_BACKUP_MIN_SECONDS", 120),
)
REMOTE_BACKUP_PULL_ON_INIT = _config_bool("HR_REMOTE_BACKUP_PULL_ON_INIT", True)
REMOTE_BACKUP_STARTER_ROW_LIMIT = max(
    0,
    _config_int("HR_REMOTE_BACKUP_STARTER_ROW_LIMIT", 5),
)
REMOTE_BACKUP_ALLOW_NONEMPTY_RESTORE = _config_bool(
    "HR_REMOTE_BACKUP_ALLOW_NONEMPTY_RESTORE",
    False,
)
REMOTE_BACKUP_ALLOW_SMALLER_PUSH = _config_bool(
    "HR_REMOTE_BACKUP_ALLOW_SMALLER_PUSH",
    False,
)


def _is_placeholder_secret(value: str) -> bool:
    token = str(value or "").strip().lower()
    if not token:
        return True
    placeholders = {"your_", "paste_", "example", "replace_me", "<", ">"}
    return any(marker in token for marker in placeholders)


REMOTE_BACKUP_ENABLED = bool(
    REMOTE_BACKUP_REPO
    and REMOTE_BACKUP_TOKEN
    and not _is_placeholder_secret(REMOTE_BACKUP_REPO)
    and not _is_placeholder_secret(REMOTE_BACKUP_TOKEN)
)
_LAST_REMOTE_PUSH_AT = 0.0
_REMOTE_BRANCH_READY = False
_LAST_STARTUP_RESTORE_INFO: dict[str, object] = {}


def _mark_startup_restore(source: str, before_rows: int, after_rows: int) -> None:
    global _LAST_STARTUP_RESTORE_INFO
    mode = str(source or "").strip().lower()
    if mode not in {"local", "cloud"}:
        mode = "local"
    _LAST_STARTUP_RESTORE_INFO = {
        "restored": True,
        "source": mode,
        "restored_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "before_rows": int(max(0, before_rows)),
        "after_rows": int(max(0, after_rows)),
        "rows_added": int(max(0, after_rows - before_rows)),
    }


def get_last_startup_restore_info() -> dict[str, object]:
    if not isinstance(_LAST_STARTUP_RESTORE_INFO, dict):
        return {}
    return dict(_LAST_STARTUP_RESTORE_INFO)


def _is_auto_restore_suppressed() -> bool:
    try:
        return AUTO_RESTORE_SUPPRESS_MARKER.exists()
    except Exception:
        return False


def _set_auto_restore_suppressed(enabled: bool) -> None:
    try:
        if enabled:
            AUTO_RESTORE_SUPPRESS_MARKER.parent.mkdir(parents=True, exist_ok=True)
            AUTO_RESTORE_SUPPRESS_MARKER.write_text(
                f"suppressed_at={datetime.now().isoformat()}",
                encoding="utf-8",
            )
        else:
            AUTO_RESTORE_SUPPRESS_MARKER.unlink(missing_ok=True)
    except Exception:
        pass


def _operational_row_count_for_path(db_path: Path | None) -> int:
    target = Path(db_path) if db_path else Path("")
    if not target.exists() or not target.is_file():
        return 0
    tables = (
        "invoices",
        "invoice_items",
        "expenses",
        "inventory_items",
        "inventory_movements",
        "inventory_purchases",
        "monthly_adjustments",
        "expense_category_budget_targets",
        "recurring_expense_templates",
        "invoice_attachments",
    )
    total = 0
    try:
        with sqlite3.connect(target) as conn:
            for table in tables:
                try:
                    row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
                    total += int(row[0]) if row else 0
                except sqlite3.Error:
                    continue
    except sqlite3.Error:
        return 0
    return int(total)


def latest_auto_backup_path() -> Path | None:
    if not AUTO_BACKUP_DIR.exists():
        return None
    candidates = sorted(
        [path for path in AUTO_BACKUP_DIR.glob("finance_hub_auto_*.db") if path.is_file()],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def _safe_backup_reason(reason: str) -> str:
    token = re.sub(r"[^a-z0-9]+", "_", str(reason or "auto").strip().lower())
    token = token.strip("_")
    return token[:24] if token else "auto"


def _prune_auto_backups() -> None:
    if not AUTO_BACKUP_DIR.exists():
        return
    backups = sorted(
        [path for path in AUTO_BACKUP_DIR.glob("finance_hub_auto_*.db") if path.is_file()],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not backups:
        return

    now_epoch = time.time()
    latest_age_seconds = 0.0
    try:
        latest_age_seconds = max(0.0, now_epoch - float(backups[0].stat().st_mtime))
    except Exception:
        latest_age_seconds = 0.0

    # If no backup activity for 2 days (default), keep only the latest snapshot.
    keep_limit = 1 if latest_age_seconds >= float(AUTO_BACKUP_INACTIVITY_SECONDS) else int(AUTO_BACKUP_KEEP)
    keep_limit = max(1, keep_limit)

    delete_targets: list[Path] = []
    for idx, old in enumerate(backups):
        if idx >= keep_limit:
            delete_targets.append(old)
            continue
        # Also expire snapshots older than inactivity window (except latest).
        if idx > 0:
            try:
                age_seconds = max(0.0, now_epoch - float(old.stat().st_mtime))
            except Exception:
                age_seconds = 0.0
            if age_seconds >= float(AUTO_BACKUP_INACTIVITY_SECONDS):
                delete_targets.append(old)

    for old in delete_targets:
        try:
            old.unlink(missing_ok=True)
        except Exception:
            pass


def sync_db_mirrors() -> list[Path]:
    """
    Mirror the primary DB into configured side-car files.
    Returns paths successfully synced.
    """
    if not DB_PATH.exists() or not DB_PATH.is_file():
        return []
    synced: list[Path] = []
    for mirror in DB_MIRROR_PATHS:
        target = Path(mirror).expanduser()
        tmp_target = target.with_name(f".{target.name}.tmp")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            source_conn = sqlite3.connect(DB_PATH)
            mirror_conn = sqlite3.connect(tmp_target)
            try:
                source_conn.backup(mirror_conn)
            finally:
                try:
                    mirror_conn.close()
                except Exception:
                    pass
                try:
                    source_conn.close()
                except Exception:
                    pass
            tmp_target.replace(target)
            synced.append(target)
        except Exception:
            try:
                tmp_target.unlink(missing_ok=True)
            except Exception:
                pass
    return synced


def _github_json_request(
    url: str,
    method: str = "GET",
    payload: dict | None = None,
) -> tuple[int, dict]:
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {REMOTE_BACKUP_TOKEN}",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "HeadlineRentalsFinanceHub",
    }
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = Request(url, data=data, headers=headers, method=method.upper())
    try:
        with urlopen(req, timeout=30) as resp:
            raw = resp.read()
            if not raw:
                return int(resp.getcode() or 200), {}
            try:
                return int(resp.getcode() or 200), json.loads(raw.decode("utf-8"))
            except Exception:
                return int(resp.getcode() or 200), {}
    except HTTPError as exc:
        try:
            raw = exc.read()
            parsed = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:
            parsed = {}
        return int(exc.code or 0), parsed
    except URLError:
        return 0, {}
    except Exception:
        return 0, {}


def _github_contents_api_url(with_ref: bool) -> str:
    file_path = quote((REMOTE_BACKUP_FILE or "").strip("/"), safe="/")
    if with_ref:
        branch_token = quote(REMOTE_BACKUP_BRANCH or "main", safe="")
        return (
            f"https://api.github.com/repos/{REMOTE_BACKUP_REPO}/contents/{file_path}"
            f"?ref={branch_token}"
        )
    return f"https://api.github.com/repos/{REMOTE_BACKUP_REPO}/contents/{file_path}"


def _ensure_remote_backup_branch_exists() -> bool:
    global _REMOTE_BRANCH_READY
    if _REMOTE_BRANCH_READY:
        return True
    if not REMOTE_BACKUP_ENABLED:
        return False
    branch = quote(REMOTE_BACKUP_BRANCH or "backup-storage", safe="")
    status_branch, _ = _github_json_request(
        f"https://api.github.com/repos/{REMOTE_BACKUP_REPO}/branches/{branch}",
        method="GET",
    )
    if status_branch == 200:
        _REMOTE_BRANCH_READY = True
        return True
    if status_branch not in {404, 422}:
        return False

    status_repo, repo_meta = _github_json_request(
        f"https://api.github.com/repos/{REMOTE_BACKUP_REPO}",
        method="GET",
    )
    if status_repo != 200:
        return False
    default_branch = str(repo_meta.get("default_branch") or "main").strip() or "main"

    status_ref, ref_meta = _github_json_request(
        f"https://api.github.com/repos/{REMOTE_BACKUP_REPO}/git/ref/heads/{quote(default_branch, safe='')}",
        method="GET",
    )
    if status_ref != 200:
        return False
    base_sha = (
        str((((ref_meta.get("object") or {}).get("sha")) or "")).strip()
    )
    if not base_sha:
        return False

    create_payload = {
        "ref": f"refs/heads/{REMOTE_BACKUP_BRANCH or 'backup-storage'}",
        "sha": base_sha,
    }
    status_create, _ = _github_json_request(
        f"https://api.github.com/repos/{REMOTE_BACKUP_REPO}/git/refs",
        method="POST",
        payload=create_payload,
    )
    if status_create in {201, 422}:
        _REMOTE_BRANCH_READY = True
        return True
    return False


def push_remote_backup_snapshot(reason: str = "auto", force: bool = False) -> bool:
    global _LAST_REMOTE_PUSH_AT
    if not REMOTE_BACKUP_ENABLED:
        return False
    if not _ensure_remote_backup_branch_exists():
        return False
    if not DB_PATH.exists() or not DB_PATH.is_file():
        return False
    local_rows = int(_operational_row_count_for_path(DB_PATH))
    if local_rows <= 0:
        return False

    now_epoch = time.time()
    if not force and (now_epoch - float(_LAST_REMOTE_PUSH_AT)) < float(REMOTE_BACKUP_MIN_SECONDS):
        return False

    try:
        payload_bytes = DB_PATH.read_bytes()
    except Exception:
        return False
    if not payload_bytes:
        return False

    status_get, meta = _github_json_request(_github_contents_api_url(with_ref=True), method="GET")
    if status_get not in {200, 404}:
        return False
    existing_sha = str(meta.get("sha") or "").strip() if status_get == 200 else ""
    if status_get == 200 and not bool(REMOTE_BACKUP_ALLOW_SMALLER_PUSH):
        remote_rows = int(_remote_backup_row_count_from_meta(meta))
        if remote_rows > local_rows:
            # Never let a tiny/fresh runtime DB overwrite a larger known-good cloud backup.
            return False

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    commit_payload = {
        "message": f"Auto DB backup ({_safe_backup_reason(reason)}) {stamp}",
        "content": base64.b64encode(payload_bytes).decode("ascii"),
        "branch": REMOTE_BACKUP_BRANCH or "main",
    }
    if existing_sha:
        commit_payload["sha"] = existing_sha

    status_put, _ = _github_json_request(
        _github_contents_api_url(with_ref=False),
        method="PUT",
        payload=commit_payload,
    )
    ok = status_put in {200, 201}
    if ok:
        _LAST_REMOTE_PUSH_AT = now_epoch
    return ok


def _download_remote_backup_bytes() -> bytes | None:
    if not REMOTE_BACKUP_ENABLED:
        return None

    status_get, meta = _github_json_request(_github_contents_api_url(with_ref=True), method="GET")
    if status_get != 200:
        return None

    blob_sha = str(meta.get("sha") or "").strip()
    if not blob_sha:
        return None

    blob_url = f"https://api.github.com/repos/{REMOTE_BACKUP_REPO}/git/blobs/{quote(blob_sha, safe='')}"
    blob_status, blob = _github_json_request(blob_url, method="GET")
    if blob_status != 200:
        return None

    if str(blob.get("encoding") or "").strip().lower() != "base64":
        return None
    encoded = str(blob.get("content") or "")
    if not encoded:
        return None
    try:
        return base64.b64decode(encoded.replace("\n", ""))
    except Exception:
        return None


def _remote_backup_row_count_from_meta(meta: dict[str, object]) -> int:
    if not isinstance(meta, dict):
        return 0
    blob_sha = str(meta.get("sha") or "").strip()
    if not blob_sha:
        return 0

    blob_url = f"https://api.github.com/repos/{REMOTE_BACKUP_REPO}/git/blobs/{quote(blob_sha, safe='')}"
    blob_status, blob = _github_json_request(blob_url, method="GET")
    if blob_status != 200:
        return 0
    if str(blob.get("encoding") or "").strip().lower() != "base64":
        return 0
    encoded = str(blob.get("content") or "")
    if not encoded:
        return 0

    tmp_path = DB_PATH.parent / f".{DB_PATH.name}.remote_count.tmp"
    try:
        tmp_path.write_bytes(base64.b64decode(encoded.replace("\n", "")))
        return int(_operational_row_count_for_path(tmp_path))
    except Exception:
        return 0
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass


def restore_remote_backup_if_needed() -> bool:
    """
    Restore DB from remote backup when local data is empty or clearly older/smaller.
    Strategy is controlled by `HR_REMOTE_BACKUP_RESTORE_MODE`:
    - `empty_only` (safe default behavior)
    - `empty_or_smaller` (legacy behavior; only applied when
      HR_REMOTE_BACKUP_ALLOW_NONEMPTY_RESTORE=1)
    """
    if not REMOTE_BACKUP_ENABLED or not REMOTE_BACKUP_PULL_ON_INIT:
        return False
    if _is_auto_restore_suppressed():
        return False

    restore_mode = (
        _config_value("HR_REMOTE_BACKUP_RESTORE_MODE", "empty_only").strip().lower()
        or "empty_only"
    )
    if restore_mode not in {"empty_only", "empty_or_smaller"}:
        restore_mode = "empty_only"

    local_rows = int(_operational_row_count_for_path(DB_PATH))

    remote_bytes = _download_remote_backup_bytes()
    if not remote_bytes:
        return False

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = DB_PATH.parent / f".{DB_PATH.name}.remote_restore.tmp"
    try:
        tmp_path.write_bytes(remote_bytes)
        remote_rows = _operational_row_count_for_path(tmp_path)
        if remote_rows <= 0:
            tmp_path.unlink(missing_ok=True)
            return False

        starter_restore = (
            local_rows <= int(REMOTE_BACKUP_STARTER_ROW_LIMIT)
            and remote_rows > local_rows
        )
        # Safety: never overwrite meaningful local data unless explicitly allowed.
        # Fresh Streamlit runtimes can contain a few starter/settings rows, so allow
        # cloud restore for that tiny state even when restore mode is `empty_only`.
        if local_rows > 0 and not bool(REMOTE_BACKUP_ALLOW_NONEMPTY_RESTORE) and not starter_restore:
            tmp_path.unlink(missing_ok=True)
            return False
        if restore_mode == "empty_only" and local_rows > 0 and not starter_restore:
            tmp_path.unlink(missing_ok=True)
            return False

        if restore_mode == "empty_or_smaller" and local_rows >= remote_rows and local_rows > 0:
            tmp_path.unlink(missing_ok=True)
            return False

        tmp_path.replace(DB_PATH)
        _set_auto_restore_suppressed(False)
        try:
            sync_db_mirrors()
        except Exception:
            pass
        return True
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
        return False


def create_db_backup_snapshot(reason: str = "auto", force: bool = False) -> Path | None:
    if not DB_PATH.exists() or not DB_PATH.is_file():
        return None
    if _operational_row_count_for_path(DB_PATH) <= 0:
        return latest_auto_backup_path()

    AUTO_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    _prune_auto_backups()
    latest = latest_auto_backup_path()
    now = datetime.now()
    if latest is not None and not force:
        # Startup should not create extra snapshots when data has not changed.
        if str(reason or "").strip().lower() == "startup":
            try:
                db_mtime = float(DB_PATH.stat().st_mtime)
                latest_mtime = float(latest.stat().st_mtime)
                if db_mtime <= (latest_mtime + 1.0):
                    return latest
            except Exception:
                pass
        try:
            age_seconds = max(0.0, now.timestamp() - float(latest.stat().st_mtime))
            if age_seconds < float(AUTO_BACKUP_MIN_SECONDS):
                return latest
        except Exception:
            pass

    stamp = now.strftime("%Y%m%d_%H%M%S")
    reason_token = _safe_backup_reason(reason)
    tmp_path = AUTO_BACKUP_DIR / f".finance_hub_auto_{stamp}_{reason_token}.tmp"
    backup_path = AUTO_BACKUP_DIR / f"finance_hub_auto_{stamp}_{reason_token}.db"

    source_conn = sqlite3.connect(DB_PATH)
    backup_conn = sqlite3.connect(tmp_path)
    try:
        source_conn.backup(backup_conn)
    finally:
        try:
            backup_conn.close()
        except Exception:
            pass
        try:
            source_conn.close()
        except Exception:
            pass

    tmp_path.replace(backup_path)
    _prune_auto_backups()
    _set_auto_restore_suppressed(False)
    try:
        # Keep a live mirror copy for users who want a Documents/project DB file
        # updated alongside every important save.
        sync_db_mirrors()
    except Exception:
        pass
    try:
        # Optional off-host persistence (e.g. Streamlit Cloud sleep/redeploy).
        push_remote_backup_snapshot(reason=reason, force=force)
    except Exception:
        pass
    return backup_path


def restore_latest_backup_if_empty() -> bool:
    if not DB_PATH.exists() or not DB_PATH.is_file():
        return False
    if _is_auto_restore_suppressed():
        return False
    if _operational_row_count_for_path(DB_PATH) > 0:
        return False

    latest = latest_auto_backup_path()
    if latest is None or not latest.exists():
        return False
    if _operational_row_count_for_path(latest) <= 0:
        return False

    try:
        shutil.copy2(latest, DB_PATH)
        _set_auto_restore_suppressed(False)
        return True
    except Exception:
        return False


def db_storage_status() -> dict[str, object]:
    latest = latest_auto_backup_path()
    startup_restore = get_last_startup_restore_info()
    latest_stamp = ""
    if latest is not None and latest.exists():
        try:
            latest_stamp = datetime.fromtimestamp(latest.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            latest_stamp = ""
    return {
        "db_path": str(DB_PATH),
        "backup_dir": str(AUTO_BACKUP_DIR),
        "latest_backup_path": str(latest) if latest else "",
        "latest_backup_at": latest_stamp,
        "db_operational_rows": _operational_row_count_for_path(DB_PATH),
        "latest_backup_rows": _operational_row_count_for_path(latest) if latest else 0,
        "mirror_paths": [str(path) for path in DB_MIRROR_PATHS],
        "remote_backup_enabled": bool(REMOTE_BACKUP_ENABLED),
        "remote_backup_configured": bool(REMOTE_BACKUP_REPO and REMOTE_BACKUP_TOKEN),
        "remote_backup_repo": REMOTE_BACKUP_REPO,
        "remote_backup_branch": REMOTE_BACKUP_BRANCH,
        "remote_backup_file": REMOTE_BACKUP_FILE,
        "remote_backup_pull_on_init": bool(REMOTE_BACKUP_PULL_ON_INIT),
        "auto_restore_suppressed": bool(_is_auto_restore_suppressed()),
        "remote_backup_restore_mode": _config_value(
            "HR_REMOTE_BACKUP_RESTORE_MODE",
            "empty_only",
        ).strip().lower(),
        "remote_backup_allow_nonempty_restore": bool(REMOTE_BACKUP_ALLOW_NONEMPTY_RESTORE),
        "startup_restore_info": startup_restore,
    }


def list_backup_snapshots(limit: int = 200) -> pd.DataFrame:
    try:
        _prune_auto_backups()
    except Exception:
        pass
    rows: list[dict[str, object]] = []
    seen: set[str] = set()

    def _append(path: Path, source: str) -> None:
        target = Path(path).expanduser()
        if not target.exists() or not target.is_file():
            return
        try:
            resolved = str(target.resolve())
        except Exception:
            resolved = str(target)
        if resolved in seen:
            return
        seen.add(resolved)
        try:
            stat = target.stat()
            modified = datetime.fromtimestamp(stat.st_mtime)
            size_bytes = int(stat.st_size)
        except Exception:
            modified = datetime.min
            size_bytes = 0
        rows.append(
            {
                "path": resolved,
                "source": source,
                "modified_at": modified,
                "rows": int(_operational_row_count_for_path(target)),
                "size_bytes": size_bytes,
            }
        )

    if AUTO_BACKUP_DIR.exists():
        for backup_file in sorted(
            [path for path in AUTO_BACKUP_DIR.glob("finance_hub_auto_*.db") if path.is_file()],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        ):
            _append(backup_file, "Auto Backup")

    for mirror in DB_MIRROR_PATHS:
        _append(mirror, "Mirror Backup")

    frame = pd.DataFrame(rows)
    if frame.empty:
        return pd.DataFrame(
            columns=["path", "source", "modified_at", "rows", "size_bytes"]
        )
    frame["modified_at"] = pd.to_datetime(frame["modified_at"], errors="coerce")
    frame = frame.sort_values("modified_at", ascending=False).head(max(1, int(limit)))
    return frame.reset_index(drop=True)


def backup_snapshot_summary(snapshot_path: str) -> dict[str, object]:
    target = Path(str(snapshot_path or "").strip()).expanduser()
    if not target.exists() or not target.is_file():
        raise ValueError("Selected backup file does not exist.")

    def _scalar(conn: sqlite3.Connection, sql: str, params: tuple[object, ...] = ()) -> object:
        try:
            row = conn.execute(sql, params).fetchone()
            if row is None:
                return 0
            return row[0]
        except sqlite3.Error:
            return 0

    summary: dict[str, object] = {
        "path": str(target),
        "size_bytes": int(target.stat().st_size),
        "operational_rows": int(_operational_row_count_for_path(target)),
        "invoices": 0,
        "confirmed_invoices": 0,
        "price_quotes": 0,
        "invoice_items": 0,
        "expenses": 0,
        "inventory_items": 0,
        "inventory_movements": 0,
        "outstanding_count": 0,
        "outstanding_total": 0.0,
    }

    with sqlite3.connect(target) as conn:
        summary["invoices"] = int(_scalar(conn, "SELECT COUNT(*) FROM invoices") or 0)
        summary["confirmed_invoices"] = int(
            _scalar(
                conn,
                """
                SELECT COUNT(*)
                FROM invoices
                WHERE lower(COALESCE(document_type, 'invoice')) = 'invoice'
                  AND lower(COALESCE(order_status, 'confirmed')) = 'confirmed'
                """,
            )
            or 0
        )
        summary["price_quotes"] = int(
            _scalar(
                conn,
                """
                SELECT COUNT(*)
                FROM invoices
                WHERE lower(COALESCE(document_type, 'invoice')) = 'quote'
                """,
            )
            or 0
        )
        summary["invoice_items"] = int(_scalar(conn, "SELECT COUNT(*) FROM invoice_items") or 0)
        summary["expenses"] = int(_scalar(conn, "SELECT COUNT(*) FROM expenses") or 0)
        summary["inventory_items"] = int(_scalar(conn, "SELECT COUNT(*) FROM inventory_items") or 0)
        summary["inventory_movements"] = int(
            _scalar(conn, "SELECT COUNT(*) FROM inventory_movements") or 0
        )
        outstanding = conn.execute(
            """
            WITH invoice_totals AS (
                SELECT
                    i.id,
                    COALESCE(i.amount_paid, 0) AS amount_paid,
                    COALESCE(SUM(COALESCE(ii.quantity, 0) * COALESCE(ii.unit_price, 0)), 0) AS invoice_total
                FROM invoices i
                LEFT JOIN invoice_items ii ON ii.invoice_id = i.id
                WHERE lower(COALESCE(i.document_type, 'invoice')) = 'invoice'
                  AND lower(COALESCE(i.order_status, 'confirmed')) = 'confirmed'
                GROUP BY i.id
            )
            SELECT
                COUNT(*),
                COALESCE(SUM(MAX(invoice_total - amount_paid, 0)), 0)
            FROM invoice_totals
            WHERE invoice_total - amount_paid > 0.01
            """
        ).fetchone()
        if outstanding:
            summary["outstanding_count"] = int(outstanding[0] or 0)
            summary["outstanding_total"] = float(outstanding[1] or 0.0)

    return summary


def restore_db_from_snapshot(snapshot_path: str) -> dict[str, object]:
    target = Path(str(snapshot_path or "").strip()).expanduser()
    if not target.exists() or not target.is_file():
        raise ValueError("Selected backup file does not exist.")

    allowed: set[str] = set()
    if AUTO_BACKUP_DIR.exists():
        for backup_file in AUTO_BACKUP_DIR.glob("finance_hub_auto_*.db"):
            try:
                allowed.add(str(backup_file.resolve()))
            except Exception:
                allowed.add(str(backup_file))
    for mirror in DB_MIRROR_PATHS:
        try:
            allowed.add(str(Path(mirror).expanduser().resolve()))
        except Exception:
            allowed.add(str(Path(mirror).expanduser()))
    try:
        selected_resolved = str(target.resolve())
    except Exception:
        selected_resolved = str(target)
    if selected_resolved not in allowed:
        raise ValueError("Selected file is not in the app backup catalog.")

    source_rows = int(_operational_row_count_for_path(target))
    if source_rows <= 0:
        raise ValueError("Selected backup has no operational rows.")

    pre_restore_snapshot = create_db_backup_snapshot(reason="pre_restore", force=True)
    tmp_path = DB_PATH.parent / f".{DB_PATH.name}.restore.tmp"

    source_conn = sqlite3.connect(target)
    restore_conn = sqlite3.connect(tmp_path)
    try:
        source_conn.backup(restore_conn)
    finally:
        try:
            restore_conn.close()
        except Exception:
            pass
        try:
            source_conn.close()
        except Exception:
            pass

    tmp_path.replace(DB_PATH)
    _set_auto_restore_suppressed(False)
    try:
        sync_db_mirrors()
    except Exception:
        pass
    try:
        push_remote_backup_snapshot(reason="restore_apply", force=True)
    except Exception:
        pass

    try:
        live_summary = backup_snapshot_summary(str(DB_PATH))
    except Exception:
        live_summary = {}

    return {
        "restored_from": selected_resolved,
        "restored_rows": int(_operational_row_count_for_path(DB_PATH)),
        "source_rows": source_rows,
        "pre_restore_snapshot": str(pre_restore_snapshot) if pre_restore_snapshot else "",
        "live_summary": live_summary,
    }


def restore_db_from_uploaded_bytes(file_bytes: bytes, original_name: str = "uploaded_backup.db") -> dict[str, object]:
    if not file_bytes:
        raise ValueError("Uploaded backup file is empty.")

    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(original_name or "uploaded_backup.db")).strip("._")
    if not safe_name:
        safe_name = "uploaded_backup.db"

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = DB_PATH.parent / f".uploaded_restore_{safe_name}"
    tmp_path.write_bytes(file_bytes)

    try:
        source_rows = int(_operational_row_count_for_path(tmp_path))
        if source_rows <= 0:
            raise ValueError("Uploaded backup has no operational rows.")

        required_tables = {
            "invoices",
            "invoice_items",
            "expenses",
            "inventory_items",
            "inventory_movements",
            "app_settings",
        }
        with sqlite3.connect(tmp_path) as check_conn:
            existing_tables = {
                str(row[0])
                for row in check_conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        missing_tables = sorted(required_tables - existing_tables)
        if missing_tables:
            raise ValueError(
                "Uploaded file is not a valid Headline Rentals backup. "
                f"Missing table(s): {', '.join(missing_tables)}."
            )

        pre_restore_snapshot = create_db_backup_snapshot(reason="pre_upload_restore", force=True)
        restore_tmp_path = DB_PATH.parent / f".{DB_PATH.name}.uploaded_restore.tmp"

        source_conn = sqlite3.connect(tmp_path)
        restore_conn = sqlite3.connect(restore_tmp_path)
        try:
            source_conn.backup(restore_conn)
        finally:
            try:
                restore_conn.close()
            except Exception:
                pass
            try:
                source_conn.close()
            except Exception:
                pass

        restore_tmp_path.replace(DB_PATH)
        _set_auto_restore_suppressed(False)
        try:
            sync_db_mirrors()
        except Exception:
            pass
        try:
            push_remote_backup_snapshot(reason="upload_restore_apply", force=True)
        except Exception:
            pass

        return {
            "restored_from": str(original_name or "uploaded backup"),
            "restored_rows": int(_operational_row_count_for_path(DB_PATH)),
            "source_rows": source_rows,
            "pre_restore_snapshot": str(pre_restore_snapshot) if pre_restore_snapshot else "",
        }
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass


def inspect_uploaded_backup_bytes(file_bytes: bytes, original_name: str = "uploaded_backup.db") -> dict[str, object]:
    if not file_bytes:
        raise ValueError("Uploaded backup file is empty.")

    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(original_name or "uploaded_backup.db")).strip("._")
    if not safe_name:
        safe_name = "uploaded_backup.db"

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = DB_PATH.parent / f".uploaded_inspect_{safe_name}"
    tmp_path.write_bytes(file_bytes)
    try:
        required_tables = {
            "invoices",
            "invoice_items",
            "expenses",
            "inventory_items",
            "inventory_movements",
            "app_settings",
        }
        with sqlite3.connect(tmp_path) as check_conn:
            existing_tables = {
                str(row[0])
                for row in check_conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            missing_tables = sorted(required_tables - existing_tables)
            if missing_tables:
                raise ValueError(
                    "Uploaded file is not a valid Headline Rentals backup. "
                    f"Missing table(s): {', '.join(missing_tables)}."
                )

            table_counts: dict[str, int] = {}
            for table_name in sorted(required_tables):
                try:
                    table_counts[table_name] = int(
                        check_conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
                    )
                except Exception:
                    table_counts[table_name] = 0

            outstanding_query = """
                SELECT COUNT(*), COALESCE(SUM(outstanding), 0)
                FROM (
                    SELECT
                        i.id,
                        COALESCE(SUM(ii.quantity * ii.unit_price), 0) - COALESCE(i.amount_paid, 0) AS outstanding
                    FROM invoices i
                    LEFT JOIN invoice_items ii ON ii.invoice_id = i.id
                    WHERE lower(COALESCE(i.document_type, '')) = 'invoice'
                      AND lower(COALESCE(i.order_status, '')) = 'confirmed'
                    GROUP BY i.id
                    HAVING outstanding > 0.01
                )
            """
            outstanding_count, outstanding_total = check_conn.execute(outstanding_query).fetchone()

        return {
            "file_name": str(original_name or "uploaded backup"),
            "size_bytes": int(len(file_bytes)),
            "operational_rows": int(_operational_row_count_for_path(tmp_path)),
            "table_counts": table_counts,
            "outstanding_count": int(outstanding_count or 0),
            "outstanding_total": float(outstanding_total or 0.0),
        }
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass

_NUMBER_WORDS = {
    "zero": "0",
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
    "ten": "10",
    "eleven": "11",
    "twelve": "12",
    "thirteen": "13",
    "fourteen": "14",
    "fifteen": "15",
    "sixteen": "16",
    "seventeen": "17",
    "eighteen": "18",
    "nineteen": "19",
    "twenty": "20",
}

_INVENTORY_NAME_STOPWORDS = {
    "a",
    "an",
    "and",
    "the",
    "for",
    "with",
    "of",
    "rental",
    "rentals",
    "service",
    "services",
    "event",
    "events",
}

_INVENTORY_PURCHASE_LABELS = {
    "inventory purchase",
    "inventory purchases",
    "stock purchase",
    "stock purchases",
    "purchase inventory",
    "purchasing new inventory",
    "new inventory purchase",
}


def _is_inventory_purchase_label(value: object) -> bool:
    text = str(value or "").strip().lower()
    return text in _INVENTORY_PURCHASE_LABELS


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db() -> None:
    # If the active DB file was accidentally replaced with an empty one,
    # recover from the latest non-empty auto-backup before migrations.
    if not _is_auto_restore_suppressed():
        rows_before_restore = _operational_row_count_for_path(DB_PATH)
        restored_local = restore_latest_backup_if_empty()
        # Optional remote recovery for hosted environments where local filesystem
        # can reset between sleeps/redeploys.
        restored_remote = restore_remote_backup_if_needed()
        if restored_local or restored_remote:
            rows_after_restore = _operational_row_count_for_path(DB_PATH)
            # If both local and cloud restored, final state is cloud.
            restore_source = "cloud" if restored_remote else "local"
            _mark_startup_restore(
                source=restore_source,
                before_rows=rows_before_restore,
                after_rows=rows_after_restore,
            )

    with get_connection() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS invoices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                invoice_number TEXT NOT NULL UNIQUE,
                event_date TEXT,
                event_time TEXT NOT NULL DEFAULT '11:00',
                rental_hours REAL NOT NULL DEFAULT 24,
                event_timezone TEXT NOT NULL DEFAULT 'America/Jamaica',
                event_location TEXT,
                document_type TEXT NOT NULL DEFAULT 'invoice',
                order_status TEXT NOT NULL DEFAULT 'confirmed',
                quote_lock INTEGER NOT NULL DEFAULT 0,
                created_by TEXT,
                source_device TEXT,
                customer_name TEXT,
                customer_phone TEXT,
                customer_email TEXT,
                contact_detail TEXT,
                delivered_to TEXT,
                paid_to TEXT,
                payment_status TEXT NOT NULL DEFAULT 'paid_full',
                amount_paid REAL NOT NULL DEFAULT 0,
                deposit_balance_enabled INTEGER NOT NULL DEFAULT 0,
                payment_notes TEXT,
                notes TEXT,
                confirmed_at TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS invoice_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                invoice_id INTEGER NOT NULL,
                item_name TEXT NOT NULL,
                item_type TEXT NOT NULL DEFAULT 'product',
                quantity REAL NOT NULL DEFAULT 1,
                unit_price REAL NOT NULL DEFAULT 0,
                unit_cost REAL NOT NULL DEFAULT 0,
                FOREIGN KEY (invoice_id) REFERENCES invoices(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS expenses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                expense_date TEXT NOT NULL,
                invoice_id INTEGER,
                category TEXT NOT NULL,
                expense_kind TEXT NOT NULL DEFAULT 'transaction',
                vendor TEXT,
                description TEXT,
                amount REAL NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (invoice_id) REFERENCES invoices(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS monthly_adjustments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                month TEXT NOT NULL,
                adjustment_type TEXT NOT NULL,
                description TEXT,
                amount REAL NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS inventory_purchases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                purchase_date TEXT NOT NULL,
                item_name TEXT,
                vendor TEXT,
                quantity REAL NOT NULL DEFAULT 0,
                amount REAL NOT NULL,
                notes TEXT,
                source_tag TEXT UNIQUE,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS monthly_budget_targets (
                month TEXT PRIMARY KEY,
                revenue_target REAL NOT NULL DEFAULT 0,
                expense_target REAL NOT NULL DEFAULT 0,
                notes TEXT,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS expense_category_budget_targets (
                month TEXT NOT NULL,
                category TEXT NOT NULL,
                amount_target REAL NOT NULL DEFAULT 0,
                notes TEXT,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (month, category)
            );

            CREATE TABLE IF NOT EXISTS recurring_expense_templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                template_name TEXT NOT NULL,
                category TEXT NOT NULL,
                vendor TEXT,
                description TEXT,
                default_amount REAL NOT NULL DEFAULT 0,
                day_of_month INTEGER NOT NULL DEFAULT 1,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS recurring_template_post_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                template_id INTEGER NOT NULL,
                month TEXT NOT NULL,
                expense_id INTEGER,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(template_id, month),
                FOREIGN KEY (template_id) REFERENCES recurring_expense_templates(id) ON DELETE CASCADE,
                FOREIGN KEY (expense_id) REFERENCES expenses(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS invoice_attachments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                invoice_id INTEGER NOT NULL,
                file_path TEXT NOT NULL,
                file_type TEXT NOT NULL,
                original_name TEXT NOT NULL,
                notes TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (invoice_id) REFERENCES invoices(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS inventory_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sku TEXT UNIQUE,
                item_name TEXT NOT NULL UNIQUE,
                category TEXT NOT NULL DEFAULT 'General',
                unit TEXT NOT NULL DEFAULT 'pcs',
                current_quantity REAL NOT NULL DEFAULT 0,
                reorder_level REAL NOT NULL DEFAULT 0,
                default_rental_price REAL NOT NULL DEFAULT 0,
                default_unit_cost REAL NOT NULL DEFAULT 0,
                unit_weight_kg REAL NOT NULL DEFAULT 0,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS inventory_movements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                inventory_item_id INTEGER NOT NULL,
                movement_date TEXT NOT NULL,
                movement_type TEXT NOT NULL,
                quantity_change REAL NOT NULL,
                unit_cost REAL,
                reference_invoice_id INTEGER,
                notes TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (inventory_item_id) REFERENCES inventory_items(id) ON DELETE CASCADE,
                FOREIGN KEY (reference_invoice_id) REFERENCES invoices(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS app_settings (
                setting_key TEXT PRIMARY KEY,
                setting_value TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS event_notification_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                invoice_id INTEGER NOT NULL,
                notification_type TEXT NOT NULL,
                sent_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(invoice_id, notification_type),
                FOREIGN KEY (invoice_id) REFERENCES invoices(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS invoice_activity_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                invoice_id INTEGER,
                invoice_number TEXT NOT NULL,
                action_type TEXT NOT NULL,
                document_type TEXT NOT NULL,
                order_status TEXT NOT NULL,
                actor_name TEXT,
                device_name TEXT,
                notes TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (invoice_id) REFERENCES invoices(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS finance_activity_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_type TEXT NOT NULL,
                entity_id INTEGER,
                action_type TEXT NOT NULL,
                actor_name TEXT,
                device_name TEXT,
                notes TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_invoices_event_date ON invoices(event_date);
            CREATE INDEX IF NOT EXISTS idx_items_invoice_id ON invoice_items(invoice_id);
            CREATE INDEX IF NOT EXISTS idx_expenses_invoice_id ON expenses(invoice_id);
            CREATE INDEX IF NOT EXISTS idx_expenses_date ON expenses(expense_date);
            CREATE INDEX IF NOT EXISTS idx_adjustments_month ON monthly_adjustments(month);
            CREATE INDEX IF NOT EXISTS idx_inventory_purchases_date ON inventory_purchases(purchase_date);
            CREATE INDEX IF NOT EXISTS idx_budget_month ON monthly_budget_targets(month);
            CREATE INDEX IF NOT EXISTS idx_expense_category_budget_month ON expense_category_budget_targets(month);
            CREATE INDEX IF NOT EXISTS idx_expense_category_budget_category ON expense_category_budget_targets(category);
            CREATE INDEX IF NOT EXISTS idx_recurring_templates_active ON recurring_expense_templates(active);
            CREATE INDEX IF NOT EXISTS idx_recurring_post_log_month ON recurring_template_post_log(month);
            CREATE INDEX IF NOT EXISTS idx_attach_invoice_id ON invoice_attachments(invoice_id);
            CREATE INDEX IF NOT EXISTS idx_inventory_name ON inventory_items(item_name);
            CREATE INDEX IF NOT EXISTS idx_inventory_active ON inventory_items(active);
            CREATE INDEX IF NOT EXISTS idx_inventory_movements_item ON inventory_movements(inventory_item_id);
            CREATE INDEX IF NOT EXISTS idx_inventory_movements_date ON inventory_movements(movement_date);
            CREATE INDEX IF NOT EXISTS idx_notification_invoice ON event_notification_log(invoice_id);
            CREATE INDEX IF NOT EXISTS idx_notification_type ON event_notification_log(notification_type);
            CREATE INDEX IF NOT EXISTS idx_invoice_activity_invoice_id ON invoice_activity_log(invoice_id);
            CREATE INDEX IF NOT EXISTS idx_invoice_activity_created_at ON invoice_activity_log(created_at);
            CREATE INDEX IF NOT EXISTS idx_finance_activity_created_at ON finance_activity_log(created_at);
            CREATE INDEX IF NOT EXISTS idx_finance_activity_entity ON finance_activity_log(entity_type, entity_id);
            """
        )
        invoice_columns = conn.execute("PRAGMA table_info(invoices)").fetchall()
        invoice_column_names = {str(row[1]) for row in invoice_columns}
        if "event_time" not in invoice_column_names:
            conn.execute(
                "ALTER TABLE invoices ADD COLUMN event_time TEXT NOT NULL DEFAULT '11:00'"
            )
        if "rental_hours" not in invoice_column_names:
            conn.execute(
                "ALTER TABLE invoices ADD COLUMN rental_hours REAL NOT NULL DEFAULT 24"
            )
        if "event_timezone" not in invoice_column_names:
            conn.execute(
                "ALTER TABLE invoices ADD COLUMN event_timezone TEXT NOT NULL DEFAULT 'America/Jamaica'"
            )
        if "event_location" not in invoice_column_names:
            conn.execute("ALTER TABLE invoices ADD COLUMN event_location TEXT")
        if "document_type" not in invoice_column_names:
            conn.execute(
                "ALTER TABLE invoices ADD COLUMN document_type TEXT NOT NULL DEFAULT 'invoice'"
            )
        if "order_status" not in invoice_column_names:
            conn.execute(
                "ALTER TABLE invoices ADD COLUMN order_status TEXT NOT NULL DEFAULT 'confirmed'"
            )
        if "quote_lock" not in invoice_column_names:
            conn.execute(
                "ALTER TABLE invoices ADD COLUMN quote_lock INTEGER NOT NULL DEFAULT 0"
            )
        if "created_by" not in invoice_column_names:
            conn.execute("ALTER TABLE invoices ADD COLUMN created_by TEXT")
        if "source_device" not in invoice_column_names:
            conn.execute("ALTER TABLE invoices ADD COLUMN source_device TEXT")
        if "customer_phone" not in invoice_column_names:
            conn.execute("ALTER TABLE invoices ADD COLUMN customer_phone TEXT")
        if "customer_email" not in invoice_column_names:
            conn.execute("ALTER TABLE invoices ADD COLUMN customer_email TEXT")
        if "payment_status" not in invoice_column_names:
            conn.execute(
                "ALTER TABLE invoices ADD COLUMN payment_status TEXT NOT NULL DEFAULT 'paid_full'"
            )
        if "amount_paid" not in invoice_column_names:
            conn.execute(
                "ALTER TABLE invoices ADD COLUMN amount_paid REAL NOT NULL DEFAULT 0"
            )
        if "deposit_balance_enabled" not in invoice_column_names:
            conn.execute(
                "ALTER TABLE invoices ADD COLUMN deposit_balance_enabled INTEGER NOT NULL DEFAULT 0"
            )
        if "payment_notes" not in invoice_column_names:
            conn.execute("ALTER TABLE invoices ADD COLUMN payment_notes TEXT")
        if "confirmed_at" not in invoice_column_names:
            conn.execute("ALTER TABLE invoices ADD COLUMN confirmed_at TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_invoices_doc_type ON invoices(document_type)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_invoices_order_status ON invoices(order_status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_invoices_quote_lock ON invoices(quote_lock)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_invoices_payment_status ON invoices(payment_status)")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_invoices_deposit_balance_enabled ON invoices(deposit_balance_enabled)"
        )

        # Backfill legacy deposit rows so Deposit Tracker can continue working
        # after the dedicated deposit flag is introduced.
        conn.execute(
            """
            UPDATE invoices
            SET deposit_balance_enabled = 1
            WHERE COALESCE(deposit_balance_enabled, 0) = 0
              AND lower(COALESCE(document_type, 'invoice')) = 'invoice'
              AND lower(COALESCE(order_status, 'confirmed')) = 'confirmed'
              AND lower(COALESCE(payment_status, 'paid_full')) = 'deposit_paid'
            """
        )

        # Remove legacy "pending invoice" mode:
        # treat those records as quotes (no finance/inventory impact path).
        conn.execute(
            """
            UPDATE invoices
            SET document_type = 'quote'
            WHERE lower(COALESCE(document_type, 'invoice')) = 'invoice'
              AND lower(COALESCE(order_status, 'confirmed')) = 'pending'
            """
        )
        conn.execute(
            """
            UPDATE invoices
            SET document_type = 'quote',
                order_status = 'pending',
                quote_lock = 1
            WHERE lower(COALESCE(document_type, 'invoice')) = 'invoice'
              AND lower(COALESCE(order_status, 'confirmed')) = 'confirmed'
              AND COALESCE(trim(event_date), '') = ''
            """
        )
        conn.execute(
            """
            UPDATE invoices
            SET quote_lock = 1
            WHERE lower(COALESCE(document_type, 'invoice')) = 'quote'
            """
        )
        # Hard safety lock: if a quote is locked, force quote state.
        conn.execute(
            """
            UPDATE invoices
            SET document_type = 'quote',
                order_status = 'pending'
            WHERE COALESCE(quote_lock, 0) = 1
            """
        )
        # Startup repair: if invoice history says the latest state for a number is quote,
        # keep it as quote even after restore/redeploy.
        conn.execute(
            """
            UPDATE invoices
            SET document_type = 'quote',
                order_status = 'pending',
                quote_lock = 1
            WHERE invoice_number IN (
                SELECT ia.invoice_number
                FROM invoice_activity_log AS ia
                JOIN (
                    SELECT invoice_number, MAX(id) AS max_id
                    FROM invoice_activity_log
                    GROUP BY invoice_number
                ) latest
                    ON latest.max_id = ia.id
                WHERE lower(COALESCE(ia.document_type, 'invoice')) = 'quote'
            )
            """
        )

        columns = conn.execute("PRAGMA table_info(expenses)").fetchall()
        column_names = {str(row[1]) for row in columns}
        if "expense_kind" not in column_names:
            conn.execute(
                "ALTER TABLE expenses ADD COLUMN expense_kind TEXT NOT NULL DEFAULT 'transaction'"
            )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_expenses_kind ON expenses(expense_kind)")

        inventory_columns = conn.execute("PRAGMA table_info(inventory_items)").fetchall()
        inventory_column_names = {str(row[1]) for row in inventory_columns}
        if "default_rental_price" not in inventory_column_names:
            conn.execute(
                "ALTER TABLE inventory_items ADD COLUMN default_rental_price REAL NOT NULL DEFAULT 0"
            )
        if "unit_weight_kg" not in inventory_column_names:
            conn.execute(
                "ALTER TABLE inventory_items ADD COLUMN unit_weight_kg REAL NOT NULL DEFAULT 0"
            )

        # Keep stored expense dates aligned to event day for invoice-linked entries.
        conn.execute(
            """
            UPDATE expenses
            SET expense_date = (
                SELECT i.event_date
                FROM invoices i
                WHERE i.id = expenses.invoice_id
            )
            WHERE expenses.invoice_id IS NOT NULL
              AND EXISTS (
                  SELECT 1
                  FROM invoices i
                  WHERE i.id = expenses.invoice_id
                    AND COALESCE(trim(i.event_date), '') <> ''
              )
            """
        )

        # Migrate legacy stock-purchase rows into dedicated inventory_purchases storage
        # so they do not affect expense/profit calculations.
        adjustment_rows = conn.execute(
            """
            SELECT id, month, adjustment_type, description, amount
            FROM monthly_adjustments
            """
        ).fetchall()
        adjustment_ids_to_delete: list[int] = []
        for row in adjustment_rows:
            if not _is_inventory_purchase_label(row["adjustment_type"]):
                continue
            month_text = str(row["month"] or "").strip()
            purchase_date = _normalize_iso_date(f"{month_text}-01") or datetime.now().date().isoformat()
            source_tag = f"legacy_adjustment:{int(row['id'])}"
            conn.execute(
                """
                INSERT OR IGNORE INTO inventory_purchases (
                    purchase_date, item_name, vendor, quantity, amount, notes, source_tag
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    purchase_date,
                    "Inventory Purchase",
                    "Legacy Monthly Adjustment",
                    0.0,
                    float(row["amount"] or 0.0),
                    str(row["description"] or "").strip(),
                    source_tag,
                ),
            )
            adjustment_ids_to_delete.append(int(row["id"]))
        if adjustment_ids_to_delete:
            conn.executemany(
                "DELETE FROM monthly_adjustments WHERE id = ?",
                [(row_id,) for row_id in adjustment_ids_to_delete],
            )

        expense_rows = conn.execute(
            """
            SELECT id, expense_date, category, vendor, description, amount
            FROM expenses
            """
        ).fetchall()
        expense_ids_to_delete: list[int] = []
        for row in expense_rows:
            if not _is_inventory_purchase_label(row["category"]):
                continue
            purchase_date = _normalize_iso_date(row["expense_date"]) or datetime.now().date().isoformat()
            source_tag = f"legacy_expense:{int(row['id'])}"
            conn.execute(
                """
                INSERT OR IGNORE INTO inventory_purchases (
                    purchase_date, item_name, vendor, quantity, amount, notes, source_tag
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    purchase_date,
                    "Inventory Purchase",
                    str(row["vendor"] or "").strip(),
                    0.0,
                    float(row["amount"] or 0.0),
                    str(row["description"] or "").strip(),
                    source_tag,
                ),
            )
            expense_ids_to_delete.append(int(row["id"]))
        if expense_ids_to_delete:
            conn.executemany(
                "DELETE FROM expenses WHERE id = ?",
                [(row_id,) for row_id in expense_ids_to_delete],
            )

    # Periodic startup snapshot (throttled by AUTO_BACKUP_MIN_SECONDS).
    try:
        create_db_backup_snapshot(reason="startup", force=False)
    except Exception:
        pass


def fetch_dataframe(query: str, params: Iterable | None = None) -> pd.DataFrame:
    with get_connection() as conn:
        return pd.read_sql_query(query, conn, params=params or ())


def upsert_invoice(
    invoice_number: str,
    event_date: str | None = None,
    event_time: str = "11:00",
    rental_hours: float = 24.0,
    event_timezone: str = "America/Jamaica",
    event_location: str = "",
    document_type: str = "invoice",
    order_status: str = "confirmed",
    created_by: str = "",
    source_device: str = "",
    customer_name: str = "",
    customer_phone: str = "",
    customer_email: str = "",
    contact_detail: str = "",
    delivered_to: str = "",
    paid_to: str = "",
    payment_status: str = "paid_full",
    amount_paid: float = 0,
    deposit_balance_enabled: bool = False,
    payment_notes: str = "",
    notes: str = "",
    force_quote_unlock: bool = False,
) -> int:
    number = (invoice_number or "").strip()
    if not number:
        raise ValueError("Invoice number is required.")
    normalized_doc_type = (document_type or "invoice").strip().lower()
    if normalized_doc_type not in {"quote", "invoice"}:
        normalized_doc_type = "invoice"
    normalized_order_status = (order_status or "confirmed").strip().lower()
    if normalized_order_status not in {"pending", "confirmed", "cancelled"}:
        normalized_order_status = "pending" if normalized_doc_type == "quote" else "confirmed"
    # Pending is quote-only. Invoices are either confirmed or cancelled.
    if normalized_doc_type == "invoice" and normalized_order_status == "pending":
        normalized_order_status = "confirmed"
    normalized_event_date = _normalize_iso_date(event_date)

    with get_connection() as conn:
        existing_row = conn.execute(
            """
            SELECT
                event_date,
                COALESCE(document_type, 'invoice') AS document_type,
                COALESCE(order_status, 'confirmed') AS order_status,
                COALESCE(quote_lock, 0) AS quote_lock,
                COALESCE(confirmed_at, '') AS confirmed_at
            FROM invoices
            WHERE invoice_number = ?
            LIMIT 1
            """,
            (number,),
        ).fetchone()
        existing_event_date = (
            _normalize_iso_date(existing_row["event_date"]) if existing_row is not None else None
        )
        existing_doc_type = (
            str(existing_row["document_type"]).strip().lower()
            if existing_row is not None and existing_row["document_type"] is not None
            else "invoice"
        )
        existing_quote_lock = (
            int(existing_row["quote_lock"] or 0)
            if existing_row is not None
            else 0
        )
        existing_order_status = (
            str(existing_row["order_status"]).strip().lower()
            if existing_row is not None and existing_row["order_status"] is not None
            else "confirmed"
        )
        existing_confirmed_at = (
            str(existing_row["confirmed_at"] or "").strip()
            if existing_row is not None
            else ""
        )
        confirmed_at_value = None
        if normalized_doc_type == "invoice" and normalized_order_status == "confirmed":
            if (
                existing_row is None
                or existing_doc_type != "invoice"
                or existing_order_status != "confirmed"
                or not existing_confirmed_at
            ):
                confirmed_at_value = datetime.utcnow().isoformat(timespec="seconds") + "Z"
            else:
                confirmed_at_value = existing_confirmed_at
        effective_event_date = normalized_event_date or existing_event_date
        if normalized_doc_type == "invoice" and normalized_order_status == "confirmed" and not effective_event_date:
            raise ValueError("Event date is required for confirmed order invoices.")
        if (
            existing_row is not None
            and existing_doc_type == "quote"
            and normalized_doc_type == "invoice"
            and not bool(force_quote_unlock)
        ):
            raise ValueError(
                "This price quote is locked. Use the explicit `Unlock + Convert` flow "
                "with Finance Password to convert to a confirmed order."
            )

        quote_lock_value = 1 if normalized_doc_type == "quote" else int(existing_quote_lock > 0)
        if normalized_doc_type == "invoice" and bool(force_quote_unlock):
            quote_lock_value = 0

        conn.execute(
            """
            INSERT INTO invoices (
                invoice_number, event_date, event_time, rental_hours, event_timezone, event_location,
                document_type, order_status, quote_lock, created_by, source_device,
                customer_name, customer_phone, customer_email, contact_detail,
                delivered_to, paid_to, payment_status, amount_paid, deposit_balance_enabled, payment_notes, notes, confirmed_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(invoice_number) DO UPDATE SET
                event_date = COALESCE(excluded.event_date, invoices.event_date),
                event_time = CASE
                    WHEN excluded.event_time <> '' THEN excluded.event_time
                    ELSE invoices.event_time
                END,
                rental_hours = CASE
                    WHEN excluded.rental_hours > 0 THEN excluded.rental_hours
                    ELSE invoices.rental_hours
                END,
                event_timezone = CASE
                    WHEN excluded.event_timezone <> '' THEN excluded.event_timezone
                    ELSE invoices.event_timezone
                END,
                event_location = CASE
                    WHEN excluded.event_location <> '' THEN excluded.event_location
                    ELSE invoices.event_location
                END,
                document_type = CASE
                    WHEN excluded.document_type IN ('quote', 'invoice') THEN excluded.document_type
                    ELSE invoices.document_type
                END,
                order_status = CASE
                    WHEN excluded.order_status IN ('pending', 'confirmed', 'cancelled') THEN excluded.order_status
                    ELSE invoices.order_status
                END,
                quote_lock = CASE
                    WHEN excluded.document_type = 'quote' THEN 1
                    WHEN excluded.document_type = 'invoice' AND excluded.quote_lock = 0 THEN 0
                    ELSE invoices.quote_lock
                END,
                created_by = CASE
                    WHEN excluded.created_by <> '' THEN excluded.created_by
                    ELSE invoices.created_by
                END,
                source_device = CASE
                    WHEN excluded.source_device <> '' THEN excluded.source_device
                    ELSE invoices.source_device
                END,
                customer_name = CASE
                    WHEN excluded.customer_name <> '' THEN excluded.customer_name
                    ELSE invoices.customer_name
                END,
                customer_phone = CASE
                    WHEN excluded.customer_phone <> '' THEN excluded.customer_phone
                    ELSE invoices.customer_phone
                END,
                customer_email = CASE
                    WHEN excluded.customer_email <> '' THEN excluded.customer_email
                    ELSE invoices.customer_email
                END,
                contact_detail = CASE
                    WHEN excluded.contact_detail <> '' THEN excluded.contact_detail
                    ELSE invoices.contact_detail
                END,
                delivered_to = CASE
                    WHEN excluded.delivered_to <> '' THEN excluded.delivered_to
                    ELSE invoices.delivered_to
                END,
                paid_to = CASE
                    WHEN excluded.paid_to <> '' THEN excluded.paid_to
                    ELSE invoices.paid_to
                END,
                payment_status = CASE
                    WHEN excluded.payment_status IN ('unpaid', 'deposit_paid', 'paid_full') THEN excluded.payment_status
                    ELSE invoices.payment_status
                END,
                amount_paid = CASE
                    WHEN excluded.amount_paid >= 0 THEN excluded.amount_paid
                    ELSE invoices.amount_paid
                END,
                deposit_balance_enabled = CASE
                    WHEN excluded.deposit_balance_enabled = 1 THEN 1
                    ELSE invoices.deposit_balance_enabled
                END,
                payment_notes = CASE
                    WHEN excluded.payment_notes <> '' THEN excluded.payment_notes
                    ELSE invoices.payment_notes
                END,
                notes = CASE
                    WHEN excluded.notes <> '' THEN excluded.notes
                    ELSE invoices.notes
                END,
                confirmed_at = CASE
                    WHEN excluded.document_type = 'invoice'
                     AND excluded.order_status = 'confirmed'
                     AND excluded.confirmed_at IS NOT NULL
                     AND excluded.confirmed_at <> ''
                     AND (
                        invoices.confirmed_at IS NULL
                        OR TRIM(invoices.confirmed_at) = ''
                        OR LOWER(COALESCE(invoices.document_type, 'invoice')) <> 'invoice'
                        OR LOWER(COALESCE(invoices.order_status, 'confirmed')) <> 'confirmed'
                     )
                    THEN excluded.confirmed_at
                    ELSE invoices.confirmed_at
                END
            """,
            (
                number,
                normalized_event_date,
                event_time.strip() or "11:00",
                float(rental_hours if rental_hours and rental_hours > 0 else 24.0),
                event_timezone.strip() or "America/Jamaica",
                event_location.strip(),
                normalized_doc_type,
                normalized_order_status,
                int(quote_lock_value > 0),
                created_by.strip(),
                source_device.strip(),
                customer_name.strip(),
                customer_phone.strip(),
                customer_email.strip(),
                contact_detail.strip(),
                delivered_to.strip(),
                paid_to.strip(),
                (payment_status or "paid_full").strip().lower(),
                float(amount_paid if amount_paid and amount_paid > 0 else 0.0),
                1 if bool(deposit_balance_enabled) else 0,
                payment_notes.strip(),
                notes.strip(),
                confirmed_at_value,
            ),
        )
        row = conn.execute(
            "SELECT id FROM invoices WHERE invoice_number = ?",
            (number,),
        ).fetchone()
        if row is None:
            raise RuntimeError("Unable to save invoice.")
        return int(row["id"])


def replace_invoice_items(invoice_id: int, items: pd.DataFrame) -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM invoice_items WHERE invoice_id = ?", (invoice_id,))
        for _, raw in items.iterrows():
            item_name = str(raw.get("item_name", "")).strip()
            if not item_name:
                continue

            quantity = float(raw.get("quantity") or 0)
            unit_price = float(raw.get("unit_price") or 0)
            unit_cost = float(raw.get("unit_cost") or 0)
            if quantity <= 0:
                continue

            item_type = str(raw.get("item_type", "product")).strip() or "product"
            conn.execute(
                """
                INSERT INTO invoice_items (
                    invoice_id, item_name, item_type, quantity, unit_price, unit_cost
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (invoice_id, item_name, item_type, quantity, unit_price, unit_cost),
            )


def add_invoice_item(
    invoice_id: int,
    item_name: str,
    item_type: str,
    quantity: float,
    unit_price: float,
    unit_cost: float = 0,
) -> int:
    with get_connection() as conn:
        existing = conn.execute(
            """
            SELECT id FROM invoice_items
            WHERE invoice_id = ?
              AND item_name = ?
              AND item_type = ?
              AND ABS(quantity - ?) < 0.0001
              AND ABS(unit_price - ?) < 0.0001
              AND ABS(unit_cost - ?) < 0.0001
            LIMIT 1
            """,
            (invoice_id, item_name, item_type, quantity, unit_price, unit_cost),
        ).fetchone()
        if existing:
            return int(existing["id"])

        cursor = conn.execute(
            """
            INSERT INTO invoice_items (
                invoice_id, item_name, item_type, quantity, unit_price, unit_cost
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (invoice_id, item_name, item_type, quantity, unit_price, unit_cost),
        )
        return int(cursor.lastrowid)


def _normalize_iso_date(value: object) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    parsed = pd.to_datetime(text, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.date().isoformat()


def _resolve_expense_date_for_storage(
    conn: sqlite3.Connection,
    expense_date: str,
    invoice_id: int | None,
) -> str:
    fallback_date = _normalize_iso_date(expense_date) or datetime.now().date().isoformat()
    if invoice_id is None:
        return fallback_date

    row = conn.execute(
        """
        SELECT event_date
        FROM invoices
        WHERE id = ?
        LIMIT 1
        """,
        (int(invoice_id),),
    ).fetchone()
    if row is None:
        return fallback_date

    event_date = _normalize_iso_date(row["event_date"])
    return event_date or fallback_date


def _confirmed_invoice_event_date(
    conn: sqlite3.Connection,
    invoice_id: int | None,
) -> str | None:
    if invoice_id is None:
        return None
    row = conn.execute(
        """
        SELECT
            event_date,
            lower(COALESCE(document_type, 'invoice')) AS document_type,
            lower(COALESCE(order_status, 'confirmed')) AS order_status
        FROM invoices
        WHERE id = ?
        LIMIT 1
        """,
        (int(invoice_id),),
    ).fetchone()
    if row is None:
        return None
    if str(row["document_type"] or "").strip() != "invoice":
        return None
    if str(row["order_status"] or "").strip() != "confirmed":
        return None
    return _normalize_iso_date(row["event_date"])


def add_expense(
    expense_date: str,
    amount: float,
    category: str,
    invoice_id: int | None = None,
    expense_kind: str = "transaction",
    vendor: str = "",
    description: str = "",
) -> int:
    if amount <= 0:
        raise ValueError("Expense amount must be positive.")
    allowed_kinds = {"transaction", "recurring_monthly", "recurring_draft", "summary_rollup", "adjustment"}
    normalized_kind = (expense_kind or "transaction").strip().lower()
    if normalized_kind not in allowed_kinds:
        raise ValueError("Invalid expense kind.")

    with get_connection() as conn:
        if normalized_kind == "transaction":
            confirmed_event_date = _confirmed_invoice_event_date(conn=conn, invoice_id=invoice_id)
            if not confirmed_event_date:
                raise ValueError(
                    "Day-to-day transaction expenses must be linked to a confirmed order invoice with an event date."
                )
            effective_expense_date = confirmed_event_date
        else:
            if invoice_id is not None:
                raise ValueError("Recurring/summary expenses cannot be linked to invoices.")
            effective_expense_date = _resolve_expense_date_for_storage(
                conn=conn,
                expense_date=expense_date,
                invoice_id=None,
            )
        existing = conn.execute(
            """
            SELECT id
            FROM expenses
            WHERE date(expense_date) = date(?)
              AND COALESCE(invoice_id, -1) = COALESCE(?, -1)
              AND lower(category) = lower(?)
              AND lower(COALESCE(expense_kind, 'transaction')) = lower(?)
              AND lower(COALESCE(vendor, '')) = lower(?)
              AND ABS(amount - ?) < 0.0001
              AND lower(COALESCE(description, '')) = lower(?)
            LIMIT 1
            """,
            (
                effective_expense_date,
                invoice_id,
                category.strip(),
                normalized_kind,
                vendor.strip(),
                amount,
                description.strip(),
            ),
        ).fetchone()
        if existing:
            return int(existing["id"])

        cursor = conn.execute(
            """
            INSERT INTO expenses (
                expense_date, invoice_id, category, expense_kind, vendor, description, amount
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                effective_expense_date,
                invoice_id,
                category.strip(),
                normalized_kind,
                vendor.strip(),
                description.strip(),
                amount,
            ),
        )
        return int(cursor.lastrowid)


def update_expense(
    expense_id: int,
    expense_date: str,
    amount: float,
    category: str,
    invoice_id: int | None = None,
    expense_kind: str = "transaction",
    vendor: str = "",
    description: str = "",
) -> None:
    if int(expense_id) <= 0:
        raise ValueError("Expense id is required.")
    if amount <= 0:
        raise ValueError("Expense amount must be positive.")
    allowed_kinds = {"transaction", "recurring_monthly", "recurring_draft", "summary_rollup", "adjustment"}
    normalized_kind = (expense_kind or "transaction").strip().lower()
    if normalized_kind not in allowed_kinds:
        raise ValueError("Invalid expense kind.")

    with get_connection() as conn:
        if normalized_kind == "transaction":
            confirmed_event_date = _confirmed_invoice_event_date(conn=conn, invoice_id=invoice_id)
            if not confirmed_event_date:
                raise ValueError(
                    "Day-to-day transaction expenses must be linked to a confirmed order invoice with an event date."
                )
            effective_expense_date = confirmed_event_date
        else:
            if invoice_id is not None:
                raise ValueError("Recurring/summary expenses cannot be linked to invoices.")
            effective_expense_date = _resolve_expense_date_for_storage(
                conn=conn,
                expense_date=expense_date,
                invoice_id=None,
            )
        cursor = conn.execute(
            """
            UPDATE expenses
            SET expense_date = ?,
                invoice_id = ?,
                category = ?,
                expense_kind = ?,
                vendor = ?,
                description = ?,
                amount = ?
            WHERE id = ?
            """,
            (
                effective_expense_date,
                invoice_id,
                category.strip(),
                normalized_kind,
                vendor.strip(),
                description.strip(),
                float(amount),
                int(expense_id),
            ),
        )
        if cursor.rowcount <= 0:
            raise ValueError("Expense record not found.")


def delete_expense(expense_id: int) -> None:
    if int(expense_id) <= 0:
        raise ValueError("Expense id is required.")
    with get_connection() as conn:
        cursor = conn.execute(
            "DELETE FROM expenses WHERE id = ?",
            (int(expense_id),),
        )
        if cursor.rowcount <= 0:
            raise ValueError("Expense record not found.")


def delete_invoice(invoice_id: int) -> dict:
    inv_id = int(invoice_id)
    if inv_id <= 0:
        raise ValueError("Invoice id is required.")

    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT id, invoice_number
            FROM invoices
            WHERE id = ?
            LIMIT 1
            """,
            (inv_id,),
        ).fetchone()
        if row is None:
            raise ValueError("Invoice not found.")

        attachment_rows = conn.execute(
            """
            SELECT file_path
            FROM invoice_attachments
            WHERE invoice_id = ?
            """,
            (inv_id,),
        ).fetchall()
        attachment_paths = [str(r["file_path"] or "").strip() for r in attachment_rows]

        # Remove auto-generated rental movements tied to this invoice to keep availability correct.
        conn.execute(
            """
            DELETE FROM inventory_movements
            WHERE reference_invoice_id = ?
              AND movement_type IN ('Auto Rental Out', 'Auto Rental Return')
            """,
            (inv_id,),
        )
        # Defensive cleanup for legacy databases where foreign-key actions may differ.
        # This keeps delete reliable even if older schema versions lacked ON DELETE clauses.
        conn.execute(
            """
            UPDATE expenses
            SET invoice_id = NULL
            WHERE invoice_id = ?
            """,
            (inv_id,),
        )
        conn.execute(
            """
            UPDATE invoice_activity_log
            SET invoice_id = NULL
            WHERE invoice_id = ?
            """,
            (inv_id,),
        )
        conn.execute(
            """
            UPDATE inventory_movements
            SET reference_invoice_id = NULL
            WHERE reference_invoice_id = ?
            """,
            (inv_id,),
        )
        conn.execute(
            "DELETE FROM event_notification_log WHERE invoice_id = ?",
            (inv_id,),
        )
        conn.execute(
            "DELETE FROM invoice_attachments WHERE invoice_id = ?",
            (inv_id,),
        )
        conn.execute(
            "DELETE FROM invoice_items WHERE invoice_id = ?",
            (inv_id,),
        )
        cursor = conn.execute("DELETE FROM invoices WHERE id = ?", (inv_id,))
        if int(cursor.rowcount or 0) <= 0:
            raise ValueError("Invoice could not be deleted.")
        return {
            "invoice_id": inv_id,
            "invoice_number": str(row["invoice_number"] or "").strip(),
            "attachment_paths": attachment_paths,
        }


def purge_all_records(preserve_settings: bool = True) -> dict:
    """
    Clear operational data for a clean restart while optionally preserving app settings.
    By default, settings (including Finance password) are preserved.
    """
    tables_to_clear = [
        "event_notification_log",
        "invoice_activity_log",
        "finance_activity_log",
        "inventory_movements",
        "recurring_template_post_log",
        "recurring_expense_templates",
        "invoice_items",
        "invoice_attachments",
        "expenses",
        "monthly_budget_targets",
        "expense_category_budget_targets",
        "monthly_adjustments",
        "inventory_purchases",
        "invoices",
        "inventory_items",
    ]
    deleted_counts: dict[str, int] = {}

    with get_connection() as conn:
        attachment_rows = conn.execute(
            "SELECT file_path FROM invoice_attachments"
        ).fetchall()
        attachment_paths = [str(r["file_path"] or "").strip() for r in attachment_rows]

        for table in tables_to_clear:
            row = conn.execute(f"SELECT COUNT(*) AS row_count FROM {table}").fetchone()
            deleted_counts[table] = int(row["row_count"]) if row is not None else 0
            conn.execute(f"DELETE FROM {table}")

        if not preserve_settings:
            row = conn.execute("SELECT COUNT(*) AS row_count FROM app_settings").fetchone()
            deleted_counts["app_settings"] = int(row["row_count"]) if row is not None else 0
            conn.execute("DELETE FROM app_settings")

        sequence_targets = tables_to_clear.copy()
        if not preserve_settings:
            sequence_targets.append("app_settings")
        for table in sequence_targets:
            conn.execute("DELETE FROM sqlite_sequence WHERE name = ?", (table,))

    # Prevent startup auto-restore from immediately repopulating data after an intentional reset.
    _set_auto_restore_suppressed(True)

    return {
        "deleted_counts": deleted_counts,
        "attachment_paths": attachment_paths,
        "preserved_settings": bool(preserve_settings),
    }


def add_monthly_adjustment(
    month: str,
    adjustment_type: str,
    amount: float,
    description: str = "",
) -> int:
    normalized_month = (month or "").strip()[:7]
    if len(normalized_month) != 7:
        raise ValueError("Month must be YYYY-MM.")
    if amount <= 0:
        raise ValueError("Adjustment amount must be positive.")

    with get_connection() as conn:
        existing = conn.execute(
            """
            SELECT id
            FROM monthly_adjustments
            WHERE month = ?
              AND lower(adjustment_type) = lower(?)
              AND ABS(amount - ?) < 0.0001
              AND lower(COALESCE(description, '')) = lower(?)
            LIMIT 1
            """,
            (normalized_month, adjustment_type.strip(), amount, description.strip()),
        ).fetchone()
        if existing:
            return int(existing["id"])

        cursor = conn.execute(
            """
            INSERT INTO monthly_adjustments (month, adjustment_type, description, amount)
            VALUES (?, ?, ?, ?)
            """,
            (normalized_month, adjustment_type.strip(), description.strip(), amount),
        )
        return int(cursor.lastrowid)


def add_inventory_purchase(
    purchase_date: str,
    amount: float,
    item_name: str = "",
    vendor: str = "",
    quantity: float = 0.0,
    notes: str = "",
) -> int:
    normalized_date = _normalize_iso_date(purchase_date)
    if not normalized_date:
        raise ValueError("Purchase date is required.")
    if float(amount or 0.0) <= 0:
        raise ValueError("Purchase amount must be positive.")
    safe_qty = max(0.0, float(quantity or 0.0))
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO inventory_purchases (
                purchase_date, item_name, vendor, quantity, amount, notes
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                normalized_date,
                str(item_name or "").strip(),
                str(vendor or "").strip(),
                safe_qty,
                float(amount),
                str(notes or "").strip(),
            ),
        )
        return int(cursor.lastrowid)


def update_inventory_purchase(
    purchase_id: int,
    purchase_date: str,
    amount: float,
    item_name: str = "",
    vendor: str = "",
    quantity: float = 0.0,
    notes: str = "",
) -> None:
    safe_id = int(purchase_id)
    if safe_id <= 0:
        raise ValueError("Purchase id is required.")
    normalized_date = _normalize_iso_date(purchase_date)
    if not normalized_date:
        raise ValueError("Purchase date is required.")
    if float(amount or 0.0) <= 0:
        raise ValueError("Purchase amount must be positive.")
    safe_qty = max(0.0, float(quantity or 0.0))
    with get_connection() as conn:
        cursor = conn.execute(
            """
            UPDATE inventory_purchases
            SET
                purchase_date = ?,
                item_name = ?,
                vendor = ?,
                quantity = ?,
                amount = ?,
                notes = ?
            WHERE id = ?
            """,
            (
                normalized_date,
                str(item_name or "").strip(),
                str(vendor or "").strip(),
                safe_qty,
                float(amount),
                str(notes or "").strip(),
                safe_id,
            ),
        )
        if cursor.rowcount <= 0:
            raise ValueError("Inventory purchase record not found.")


def delete_inventory_purchase(purchase_id: int) -> None:
    safe_id = int(purchase_id)
    if safe_id <= 0:
        raise ValueError("Purchase id is required.")
    with get_connection() as conn:
        cursor = conn.execute(
            "DELETE FROM inventory_purchases WHERE id = ?",
            (safe_id,),
        )
        if cursor.rowcount <= 0:
            raise ValueError("Inventory purchase record not found.")


def load_inventory_purchases() -> pd.DataFrame:
    return fetch_dataframe(
        """
        SELECT
            id,
            purchase_date,
            COALESCE(item_name, '') AS item_name,
            COALESCE(vendor, '') AS vendor,
            COALESCE(quantity, 0) AS quantity,
            COALESCE(amount, 0) AS amount,
            COALESCE(notes, '') AS notes,
            created_at
        FROM inventory_purchases
        ORDER BY date(purchase_date) DESC, id DESC
        """
    )


def invoice_options(
    include_quotes: bool = True,
    confirmed_only: bool = False,
) -> pd.DataFrame:
    where_parts: list[str] = []
    if not include_quotes:
        where_parts.append("lower(COALESCE(document_type, 'invoice')) = 'invoice'")
    if confirmed_only:
        where_parts.append("lower(COALESCE(order_status, 'confirmed')) = 'confirmed'")

    where_clause = ""
    if where_parts:
        where_clause = "WHERE " + " AND ".join(where_parts)

    return fetch_dataframe(
        f"""
        SELECT
            id,
            invoice_number,
            COALESCE(event_date, '') AS event_date,
            COALESCE(event_time, '11:00') AS event_time,
            COALESCE(document_type, 'invoice') AS document_type,
            COALESCE(order_status, 'confirmed') AS order_status,
            COALESCE(payment_status, 'paid_full') AS payment_status,
            COALESCE(amount_paid, 0) AS amount_paid,
            COALESCE(deposit_balance_enabled, 0) AS deposit_balance_enabled,
            COALESCE(created_by, '') AS created_by,
            COALESCE(source_device, '') AS source_device,
            COALESCE(customer_name, '') AS customer_name
        FROM invoices
        {where_clause}
        ORDER BY
            CASE WHEN event_date IS NULL THEN 1 ELSE 0 END,
            event_date DESC,
            invoice_number DESC
        """
    )


def invoice_meta_by_number(invoice_number: str) -> dict | None:
    number = (invoice_number or "").strip()
    if not number:
        return None
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT
                id,
                invoice_number,
                COALESCE(event_date, '') AS event_date,
                COALESCE(document_type, 'invoice') AS document_type,
                COALESCE(order_status, 'confirmed') AS order_status,
                COALESCE(quote_lock, 0) AS quote_lock,
                COALESCE(payment_status, 'paid_full') AS payment_status,
                COALESCE(amount_paid, 0) AS amount_paid,
                COALESCE(deposit_balance_enabled, 0) AS deposit_balance_enabled,
                COALESCE(confirmed_at, '') AS confirmed_at
            FROM invoices
            WHERE invoice_number = ?
            LIMIT 1
            """,
            (number,),
        ).fetchone()
    if row is None:
        return None
    return dict(row)


def invoice_export_bundle(invoice_id: int) -> tuple[dict, pd.DataFrame]:
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT
                id,
                invoice_number,
                COALESCE(event_date, '') AS event_date,
                COALESCE(event_time, '11:00') AS event_time,
                COALESCE(rental_hours, 24) AS rental_hours,
                COALESCE(event_timezone, 'America/Jamaica') AS event_timezone,
                COALESCE(NULLIF(event_location, ''), delivered_to, '') AS event_location,
                COALESCE(document_type, 'invoice') AS document_type,
                COALESCE(order_status, 'confirmed') AS order_status,
                COALESCE(created_by, '') AS created_by,
                COALESCE(source_device, '') AS source_device,
                COALESCE(customer_name, '') AS customer_name,
                COALESCE(customer_phone, '') AS customer_phone,
                COALESCE(customer_email, '') AS customer_email,
                COALESCE(contact_detail, '') AS contact_detail,
                COALESCE(delivered_to, '') AS delivered_to,
                COALESCE(paid_to, '') AS paid_to,
                COALESCE(payment_status, 'paid_full') AS payment_status,
                COALESCE(amount_paid, 0) AS amount_paid,
                COALESCE(deposit_balance_enabled, 0) AS deposit_balance_enabled,
                COALESCE(payment_notes, '') AS payment_notes,
                COALESCE(notes, '') AS notes,
                COALESCE(confirmed_at, '') AS confirmed_at
            FROM invoices
            WHERE id = ?
            LIMIT 1
            """,
            (int(invoice_id),),
        ).fetchone()
        if row is None:
            raise ValueError("Invoice not found.")

        items = pd.read_sql_query(
            """
            SELECT
                COALESCE(item_name, '') AS item_name,
                COALESCE(item_type, 'product') AS item_type,
                COALESCE(quantity, 0) AS quantity,
                COALESCE(unit_price, 0) AS unit_price,
                COALESCE(unit_cost, 0) AS unit_cost,
                COALESCE(quantity, 0) * COALESCE(unit_price, 0) AS line_total
            FROM invoice_items
            WHERE invoice_id = ?
            ORDER BY id ASC
            """,
            conn,
            params=(int(invoice_id),),
        )
    return dict(row), items


def add_invoice_attachment(
    invoice_id: int,
    file_path: str,
    file_type: str,
    original_name: str,
    notes: str = "",
) -> int:
    with get_connection() as conn:
        existing = conn.execute(
            """
            SELECT id
            FROM invoice_attachments
            WHERE invoice_id = ?
              AND file_path = ?
            LIMIT 1
            """,
            (invoice_id, file_path),
        ).fetchone()
        if existing:
            return int(existing["id"])

        cursor = conn.execute(
            """
            INSERT INTO invoice_attachments (
                invoice_id, file_path, file_type, original_name, notes
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (invoice_id, file_path, file_type, original_name, notes.strip()),
        )
        return int(cursor.lastrowid)


def load_invoice_attachments(invoice_id: int) -> pd.DataFrame:
    return fetch_dataframe(
        """
        SELECT id, invoice_id, file_path, file_type, original_name, notes, created_at
        FROM invoice_attachments
        WHERE invoice_id = ?
        ORDER BY created_at DESC
        """,
        (invoice_id,),
    )


def delete_invoice_attachment(attachment_id: int) -> dict:
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT id, invoice_id, file_path, file_type, original_name
            FROM invoice_attachments
            WHERE id = ?
            LIMIT 1
            """,
            (int(attachment_id),),
        ).fetchone()
        if row is None:
            raise ValueError("Attachment not found.")

        conn.execute(
            "DELETE FROM invoice_attachments WHERE id = ?",
            (int(attachment_id),),
        )
    return dict(row)


def upcoming_invoices(days_ahead: int = 14) -> pd.DataFrame:
    with get_connection() as conn:
        return pd.read_sql_query(
            """
            SELECT
                i.id,
                i.invoice_number,
                i.event_date,
                COALESCE(i.event_time, '11:00') AS event_time,
                COALESCE(i.rental_hours, 24) AS rental_hours,
                COALESCE(NULLIF(i.event_location, ''), i.delivered_to, '') AS event_location,
                COALESCE(i.customer_name, '') AS customer_name,
                COALESCE(i.contact_detail, '') AS contact_detail,
                COALESCE(SUM(it.quantity * it.unit_price), 0) AS revenue
            FROM invoices i
            LEFT JOIN invoice_items it ON it.invoice_id = i.id
            WHERE lower(COALESCE(i.document_type, 'invoice')) = 'invoice'
              AND lower(COALESCE(i.order_status, 'confirmed')) = 'confirmed'
              AND date(i.event_date) >= date('now', 'localtime')
              AND date(i.event_date) <= date('now', 'localtime', '+' || ? || ' day')
            GROUP BY i.id
            ORDER BY date(i.event_date) ASC
            """,
            conn,
            params=(days_ahead,),
        )


def log_invoice_activity(
    invoice_id: int,
    invoice_number: str,
    action_type: str,
    document_type: str,
    order_status: str,
    actor_name: str = "",
    device_name: str = "",
    notes: str = "",
) -> int:
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO invoice_activity_log (
                invoice_id,
                invoice_number,
                action_type,
                document_type,
                order_status,
                actor_name,
                device_name,
                notes
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(invoice_id),
                (invoice_number or "").strip(),
                (action_type or "updated").strip().lower(),
                (document_type or "invoice").strip().lower(),
                (order_status or "confirmed").strip().lower(),
                (actor_name or "").strip(),
                (device_name or "").strip(),
                (notes or "").strip(),
            ),
        )
    return int(cursor.lastrowid)


def set_invoice_payment_status(
    invoice_id: int,
    payment_status: str,
    amount_paid: float,
    payment_notes: str = "",
) -> None:
    normalized = (payment_status or "").strip().lower()
    if normalized not in {"unpaid", "deposit_paid", "paid_full"}:
        raise ValueError("Invalid payment status.")
    safe_paid = max(0.0, float(amount_paid or 0.0))
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE invoices
            SET
                payment_status = ?,
                amount_paid = ?,
                payment_notes = CASE
                    WHEN ? <> '' THEN ?
                    ELSE payment_notes
                END
            WHERE id = ?
            """,
            (normalized, safe_paid, payment_notes.strip(), payment_notes.strip(), int(invoice_id)),
        )


def load_invoice_build_log(limit: int = 200) -> pd.DataFrame:
    return fetch_dataframe(
        """
        SELECT
            id,
            invoice_id,
            invoice_number,
            action_type,
            document_type,
            order_status,
            actor_name,
            device_name,
            notes,
            created_at
        FROM invoice_activity_log
        ORDER BY datetime(created_at) DESC, id DESC
        LIMIT ?
        """,
        (int(limit),),
    )


def expense_total_for_invoice_category(
    invoice_id: int,
    category: str,
    excluded_vendors: tuple[str, ...] = (),
) -> float:
    params: list = [invoice_id, category.strip()]
    vendor_clause = ""
    if excluded_vendors:
        placeholders = ", ".join(["?"] * len(excluded_vendors))
        vendor_clause = f" AND COALESCE(vendor, '') NOT IN ({placeholders})"
        params.extend(excluded_vendors)

    query = f"""
        SELECT COALESCE(SUM(amount), 0) AS total
        FROM expenses
        WHERE invoice_id = ?
          AND lower(category) = lower(?)
          {vendor_clause}
    """

    with get_connection() as conn:
        row = conn.execute(query, tuple(params)).fetchone()
    if row is None:
        return 0.0
    return float(row["total"] or 0.0)


def cleanup_legacy_double_counts() -> dict[str, int]:
    monthly_rollup_cols = (
        "Re-Rental",
        "Wages",
        "Bad Debt",
        "Petrol (Add By Invoice #)",
        "Unforseen Expenses",
        "Unforeseen Expenses",
    )
    result = {
        "removed_monthly_rollups": 0,
        "removed_working_expense_imports": 0,
        "removed_legacy_invoice_summaries": 0,
    }

    with get_connection() as conn:
        placeholders = ", ".join(["?"] * len(monthly_rollup_cols))
        result["removed_monthly_rollups"] = conn.execute(
            f"""
            DELETE FROM expenses
            WHERE vendor = 'Monthly Ledger'
              AND category IN ({placeholders})
              AND lower(COALESCE(expense_kind, 'transaction')) <> 'summary_rollup'
            """,
            monthly_rollup_cols,
        ).rowcount

        result["removed_working_expense_imports"] = conn.execute(
            """
            DELETE FROM expenses
            WHERE category = 'Working Expense (Imported)'
            """
        ).rowcount

        result["removed_legacy_invoice_summaries"] = conn.execute(
            """
            DELETE FROM expenses
            WHERE vendor = 'Legacy Sheet'
              AND category IN ('Wages', 'Re-Rental')
              AND invoice_id IS NOT NULL
              AND EXISTS (
                SELECT 1
                FROM expenses d
                WHERE d.invoice_id = expenses.invoice_id
                  AND lower(d.category) = lower(expenses.category)
                  AND COALESCE(d.vendor, '') NOT IN (
                    'Legacy Sheet',
                    'Summary Adjustment',
                    'Monthly Ledger',
                    ''
                  )
              )
            """
        ).rowcount

    return result


def get_setting(key: str, default: str = "") -> str:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT setting_value FROM app_settings WHERE setting_key = ?",
            (key.strip(),),
        ).fetchone()
    if row is None:
        return default
    return str(row["setting_value"])


def set_setting(key: str, value: str) -> None:
    setting_key = key.strip()
    if not setting_key:
        raise ValueError("Setting key is required.")
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO app_settings (setting_key, setting_value, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(setting_key) DO UPDATE SET
                setting_value = excluded.setting_value,
                updated_at = CURRENT_TIMESTAMP
            """,
            (setting_key, str(value)),
        )


def log_finance_activity(
    entity_type: str,
    entity_id: int | None,
    action_type: str,
    actor_name: str = "",
    device_name: str = "",
    notes: str = "",
) -> int:
    entity = (entity_type or "").strip().lower() or "general"
    action = (action_type or "").strip().lower() or "updated"
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO finance_activity_log (
                entity_type, entity_id, action_type, actor_name, device_name, notes
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                entity,
                None if entity_id is None else int(entity_id),
                action,
                (actor_name or "").strip(),
                (device_name or "").strip(),
                (notes or "").strip(),
            ),
        )
    return int(cursor.lastrowid)


def load_finance_activity(limit: int = 300) -> pd.DataFrame:
    return fetch_dataframe(
        """
        SELECT
            id,
            entity_type,
            entity_id,
            action_type,
            actor_name,
            device_name,
            notes,
            created_at
        FROM finance_activity_log
        ORDER BY datetime(created_at) DESC, id DESC
        LIMIT ?
        """,
        (int(limit),),
    )


def _normalize_month_token(month: str) -> str:
    token = (month or "").strip()[:7]
    if len(token) != 7 or "-" not in token:
        raise ValueError("Month must be YYYY-MM.")
    return token


def upsert_monthly_budget(
    month: str,
    revenue_target: float,
    expense_target: float,
    notes: str = "",
) -> str:
    month_token = _normalize_month_token(month)
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO monthly_budget_targets (
                month, revenue_target, expense_target, notes, updated_at
            )
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(month) DO UPDATE SET
                revenue_target = excluded.revenue_target,
                expense_target = excluded.expense_target,
                notes = excluded.notes,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                month_token,
                float(revenue_target or 0.0),
                float(expense_target or 0.0),
                (notes or "").strip(),
            ),
        )
    return month_token


def delete_monthly_budget(month: str) -> bool:
    month_token = _normalize_month_token(month)
    with get_connection() as conn:
        cursor = conn.execute(
            "DELETE FROM monthly_budget_targets WHERE month = ?",
            (month_token,),
        )
    return bool(cursor.rowcount and cursor.rowcount > 0)


def load_monthly_budgets() -> pd.DataFrame:
    return fetch_dataframe(
        """
        SELECT
            month,
            COALESCE(revenue_target, 0) AS revenue_target,
            COALESCE(expense_target, 0) AS expense_target,
            COALESCE(notes, '') AS notes,
            updated_at
        FROM monthly_budget_targets
        ORDER BY month ASC
        """
    )


def upsert_expense_category_budget(
    month: str,
    category: str,
    amount_target: float,
    notes: str = "",
) -> tuple[str, str]:
    month_token = _normalize_month_token(month)
    category_token = str(category or "").strip()
    if not category_token:
        raise ValueError("Category is required.")
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO expense_category_budget_targets (
                month, category, amount_target, notes, updated_at
            )
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(month, category) DO UPDATE SET
                amount_target = excluded.amount_target,
                notes = excluded.notes,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                month_token,
                category_token,
                float(amount_target or 0.0),
                str(notes or "").strip(),
            ),
        )
    return month_token, category_token


def delete_expense_category_budget(month: str, category: str) -> bool:
    month_token = _normalize_month_token(month)
    category_token = str(category or "").strip()
    if not category_token:
        raise ValueError("Category is required.")
    with get_connection() as conn:
        cursor = conn.execute(
            """
            DELETE FROM expense_category_budget_targets
            WHERE month = ? AND category = ?
            """,
            (month_token, category_token),
        )
    return bool(cursor.rowcount and cursor.rowcount > 0)


def load_expense_category_budgets() -> pd.DataFrame:
    return fetch_dataframe(
        """
        SELECT
            month,
            COALESCE(category, '') AS category,
            COALESCE(amount_target, 0) AS amount_target,
            COALESCE(notes, '') AS notes,
            updated_at
        FROM expense_category_budget_targets
        ORDER BY month ASC, category ASC
        """
    )


def upsert_recurring_expense_template(
    template_name: str,
    category: str,
    default_amount: float,
    vendor: str = "",
    description: str = "",
    day_of_month: int = 1,
    active: int = 1,
) -> int:
    name = (template_name or "").strip()
    if not name:
        raise ValueError("Template name is required.")
    cat = (category or "").strip() or "Other"
    amount = float(default_amount or 0.0)
    if amount <= 0:
        raise ValueError("Template amount must be greater than zero.")
    day_token = int(day_of_month or 1)
    if day_token < 1:
        day_token = 1
    if day_token > 31:
        day_token = 31

    with get_connection() as conn:
        existing = conn.execute(
            """
            SELECT id
            FROM recurring_expense_templates
            WHERE lower(trim(template_name)) = lower(trim(?))
            LIMIT 1
            """,
            (name,),
        ).fetchone()
        if existing is None:
            cursor = conn.execute(
                """
                INSERT INTO recurring_expense_templates (
                    template_name,
                    category,
                    vendor,
                    description,
                    default_amount,
                    day_of_month,
                    active,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (
                    name,
                    cat,
                    (vendor or "").strip(),
                    (description or "").strip(),
                    amount,
                    day_token,
                    int(1 if active else 0),
                ),
            )
            return int(cursor.lastrowid)

        template_id = int(existing["id"])
        conn.execute(
            """
            UPDATE recurring_expense_templates
            SET
                category = ?,
                vendor = ?,
                description = ?,
                default_amount = ?,
                day_of_month = ?,
                active = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                cat,
                (vendor or "").strip(),
                (description or "").strip(),
                amount,
                day_token,
                int(1 if active else 0),
                template_id,
            ),
        )
        return template_id


def delete_recurring_expense_template(template_id: int) -> bool:
    with get_connection() as conn:
        cursor = conn.execute(
            "DELETE FROM recurring_expense_templates WHERE id = ?",
            (int(template_id),),
        )
    return bool(cursor.rowcount and cursor.rowcount > 0)


def load_recurring_expense_templates(active_only: bool = False) -> pd.DataFrame:
    where_clause = "WHERE active = 1" if active_only else ""
    return fetch_dataframe(
        f"""
        SELECT
            id,
            COALESCE(template_name, '') AS template_name,
            COALESCE(category, 'Other') AS category,
            COALESCE(vendor, '') AS vendor,
            COALESCE(description, '') AS description,
            COALESCE(default_amount, 0) AS default_amount,
            COALESCE(day_of_month, 1) AS day_of_month,
            COALESCE(active, 1) AS active,
            created_at,
            updated_at
        FROM recurring_expense_templates
        {where_clause}
        ORDER BY lower(template_name) ASC
        """
    )


def run_recurring_template_autopost(reference_date: str | None = None) -> dict[str, int | str]:
    ref = pd.to_datetime(reference_date or datetime.now().date().isoformat(), errors="coerce")
    if pd.isna(ref):
        ref = pd.Timestamp.now()
    year = int(ref.year)
    month_num = int(ref.month)
    month_token = f"{year:04d}-{month_num:02d}"
    month_last_day = int(calendar.monthrange(year, month_num)[1])

    posted_count = 0
    reused_count = 0
    skipped_count = 0
    templates_seen = 0
    post_log_rows = 0

    with get_connection() as conn:
        templates = conn.execute(
            """
            SELECT
                id,
                template_name,
                category,
                vendor,
                description,
                default_amount,
                day_of_month
            FROM recurring_expense_templates
            WHERE active = 1
            ORDER BY id ASC
            """
        ).fetchall()
        templates_seen = len(templates)

        for row in templates:
            template_id = int(row["id"])
            existing_post = conn.execute(
                """
                SELECT id, expense_id
                FROM recurring_template_post_log
                WHERE template_id = ?
                  AND month = ?
                LIMIT 1
                """,
                (template_id, month_token),
            ).fetchone()
            if existing_post is not None:
                skipped_count += 1
                continue

            amount = float(row["default_amount"] or 0.0)
            if amount <= 0:
                skipped_count += 1
                continue

            raw_dom = int(row["day_of_month"] or 1)
            if raw_dom < 1:
                raw_dom = 1
            if raw_dom > month_last_day:
                raw_dom = month_last_day
            expense_date = f"{year:04d}-{month_num:02d}-{raw_dom:02d}"
            category = str(row["category"] or "").strip() or "Other"
            vendor = str(row["vendor"] or "").strip()
            description = str(row["description"] or "").strip()

            existing_expense = conn.execute(
                """
                SELECT id
                FROM expenses
                WHERE date(expense_date) = date(?)
                  AND invoice_id IS NULL
                  AND lower(COALESCE(category, '')) = lower(?)
                  AND lower(COALESCE(expense_kind, 'transaction')) IN ('recurring_monthly', 'recurring_draft')
                  AND lower(COALESCE(vendor, '')) = lower(?)
                  AND lower(COALESCE(description, '')) = lower(?)
                ORDER BY id DESC
                LIMIT 1
                """,
                (expense_date, category, vendor, description),
            ).fetchone()

            if existing_expense is None:
                cursor = conn.execute(
                    """
                    INSERT INTO expenses (
                        expense_date, invoice_id, category, expense_kind, vendor, description, amount
                    )
                    VALUES (?, NULL, ?, 'recurring_draft', ?, ?, ?)
                    """,
                    (
                        expense_date,
                        category,
                        vendor,
                        description,
                        amount,
                    ),
                )
                expense_id = int(cursor.lastrowid)
                posted_count += 1
            else:
                expense_id = int(existing_expense["id"])
                reused_count += 1

            cursor_post = conn.execute(
                """
                INSERT INTO recurring_template_post_log (template_id, month, expense_id, created_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(template_id, month) DO NOTHING
                """,
                (template_id, month_token, expense_id),
            )
            if cursor_post.rowcount and int(cursor_post.rowcount) > 0:
                post_log_rows += int(cursor_post.rowcount)

        if posted_count > 0:
            conn.execute(
                """
                INSERT INTO finance_activity_log (
                    entity_type, entity_id, action_type, actor_name, device_name, notes
                )
                VALUES ('recurring_autopost', NULL, 'run', 'System', 'Auto', ?)
                """,
                (f"{month_token}: posted {posted_count} recurring draft expense(s).",),
            )

    return {
        "month": month_token,
        "templates_seen": templates_seen,
        "posted_count": posted_count,
        "reused_count": reused_count,
        "skipped_count": skipped_count,
        "post_log_rows": post_log_rows,
    }


def load_recurring_draft_expenses(month: str | None = None) -> pd.DataFrame:
    month_token = str(month or "").strip()
    params: tuple = ()
    where_clause = ""
    if month_token:
        where_clause = "AND strftime('%Y-%m', e.expense_date) = ?"
        params = (month_token,)
    return fetch_dataframe(
        f"""
        SELECT
            e.id,
            e.expense_date,
            COALESCE(e.category, '') AS category,
            COALESCE(e.vendor, '') AS vendor,
            COALESCE(e.description, '') AS description,
            COALESCE(e.amount, 0) AS amount,
            COALESCE(t.id, 0) AS template_id,
            COALESCE(t.template_name, '') AS template_name,
            COALESCE(t.day_of_month, 1) AS template_day,
            COALESCE(r.month, strftime('%Y-%m', e.expense_date)) AS post_month
        FROM expenses e
        LEFT JOIN recurring_template_post_log r ON r.expense_id = e.id
        LEFT JOIN recurring_expense_templates t ON t.id = r.template_id
        WHERE lower(COALESCE(e.expense_kind, 'transaction')) = 'recurring_draft'
          {where_clause}
        ORDER BY date(e.expense_date) DESC, e.id DESC
        """,
        params=params,
    )


def finalize_recurring_draft_expense(
    expense_id: int,
    actual_amount: float,
    actual_date: str,
    note_suffix: str = "",
) -> int:
    target_id = int(expense_id)
    if target_id <= 0:
        raise ValueError("Expense id is required.")
    amount = float(actual_amount or 0.0)
    if amount <= 0:
        raise ValueError("Actual amount must be greater than 0.")
    normalized_date = _normalize_iso_date(actual_date) or datetime.now().date().isoformat()

    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT id, description
            FROM expenses
            WHERE id = ?
              AND lower(COALESCE(expense_kind, 'transaction')) = 'recurring_draft'
            LIMIT 1
            """,
            (target_id,),
        ).fetchone()
        if row is None:
            raise ValueError("Recurring draft expense not found.")
        current_desc = str(row["description"] or "").strip()
        suffix = str(note_suffix or "").strip()
        merged_desc = current_desc
        if suffix:
            merged_desc = f"{current_desc} | {suffix}" if current_desc else suffix

        cursor = conn.execute(
            """
            UPDATE expenses
            SET amount = ?,
                expense_date = ?,
                expense_kind = 'recurring_monthly',
                description = ?
            WHERE id = ?
            """,
            (amount, normalized_date, merged_desc, target_id),
        )
        if cursor.rowcount <= 0:
            raise ValueError("Could not finalize recurring draft expense.")
        return target_id


def find_similar_expense_candidates(
    expense_date: str,
    amount: float,
    category: str,
    invoice_id: int | None = None,
    vendor: str = "",
    description: str = "",
    window_days: int = 14,
    exclude_expense_id: int | None = None,
    limit: int = 8,
) -> pd.DataFrame:
    safe_date = _normalize_iso_date(expense_date) or datetime.now().date().isoformat()
    safe_amount = float(amount or 0.0)
    if safe_amount <= 0:
        return pd.DataFrame(
            columns=[
                "id",
                "expense_date",
                "invoice_id",
                "category",
                "expense_kind",
                "vendor",
                "description",
                "amount",
            ]
        )
    safe_category = (category or "").strip()
    safe_vendor = (vendor or "").strip().lower()
    safe_description = (description or "").strip().lower()
    window = max(1, int(window_days))
    row_limit = max(1, int(limit))

    filters = [
        "lower(COALESCE(category, '')) = lower(?)",
        "ABS(amount - ?) < 0.0001",
        "date(expense_date) BETWEEN date(?, '-' || ? || ' day') AND date(?, '+' || ? || ' day')",
    ]
    params: list[object] = [safe_category, safe_amount, safe_date, window, safe_date, window]

    if invoice_id is not None:
        filters.append("COALESCE(invoice_id, -1) = COALESCE(?, -1)")
        params.append(int(invoice_id))
    if safe_vendor:
        filters.append("lower(COALESCE(vendor, '')) = ?")
        params.append(safe_vendor)
    elif safe_description:
        filters.append("lower(COALESCE(description, '')) = ?")
        params.append(safe_description)
    if exclude_expense_id is not None:
        filters.append("id <> ?")
        params.append(int(exclude_expense_id))

    where_clause = " AND ".join(filters)
    params.extend([safe_date, row_limit])

    return fetch_dataframe(
        f"""
        SELECT
            id,
            expense_date,
            invoice_id,
            category,
            expense_kind,
            COALESCE(vendor, '') AS vendor,
            COALESCE(description, '') AS description,
            amount
        FROM expenses
        WHERE {where_clause}
        ORDER BY ABS(julianday(date(expense_date)) - julianday(date(?))) ASC, id DESC
        LIMIT ?
        """,
        tuple(params),
    )


def upsert_inventory_item(
    item_name: str,
    sku: str = "",
    category: str = "General",
    unit: str = "pcs",
    reorder_level: float = 0,
    default_rental_price: float = 0,
    default_unit_cost: float = 0,
    unit_weight_kg: float = 0,
    active: int = 1,
) -> int:
    name = (item_name or "").strip()
    normalized_sku = (sku or "").strip() or None
    if not name:
        raise ValueError("Inventory item name is required.")
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO inventory_items (
                sku, item_name, category, unit, reorder_level, default_rental_price, default_unit_cost, unit_weight_kg, active, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(item_name) DO UPDATE SET
                sku = CASE WHEN excluded.sku <> '' THEN excluded.sku ELSE inventory_items.sku END,
                category = excluded.category,
                unit = excluded.unit,
                reorder_level = excluded.reorder_level,
                default_rental_price = excluded.default_rental_price,
                default_unit_cost = excluded.default_unit_cost,
                unit_weight_kg = excluded.unit_weight_kg,
                active = excluded.active,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                normalized_sku,
                name,
                (category or "General").strip(),
                (unit or "pcs").strip(),
                float(reorder_level or 0),
                float(default_rental_price or 0),
                float(default_unit_cost or 0),
                float(unit_weight_kg or 0),
                int(1 if active else 0),
            ),
        )
        row = conn.execute(
            "SELECT id FROM inventory_items WHERE item_name = ?",
            (name,),
        ).fetchone()
    if row is None:
        raise RuntimeError("Could not save inventory item.")
    return int(row["id"])


def update_inventory_item_values(
    item_id: int,
    item_name: str,
    category: str,
    unit: str,
    reorder_level: float,
    default_rental_price: float,
    active: int,
    quantity_change: float = 0.0,
    target_quantity: float | None = None,
    movement_notes: str = "Adjusted from Inventory Price List editor.",
) -> int:
    inv_id = int(item_id)
    name = (item_name or "").strip()
    if not name:
        raise ValueError("Item name is required.")

    qty_delta = float(quantity_change or 0.0)
    target_qty = None if target_quantity is None else float(target_quantity)
    with get_connection() as conn:
        current = conn.execute(
            """
            SELECT id, current_quantity
            FROM inventory_items
            WHERE id = ?
            LIMIT 1
            """,
            (inv_id,),
        ).fetchone()
        if current is None:
            raise ValueError("Inventory item not found.")

        duplicate = conn.execute(
            """
            SELECT id
            FROM inventory_items
            WHERE lower(trim(item_name)) = lower(trim(?))
              AND id <> ?
            LIMIT 1
            """,
            (name, inv_id),
        ).fetchone()
        if duplicate is not None:
            raise ValueError(f"An inventory item named '{name}' already exists.")

        current_qty = float(current["current_quantity"] or 0.0)
        if target_qty is not None:
            safe_target = max(0.0, float(target_qty))
            next_qty = safe_target
            qty_delta = safe_target - current_qty
        else:
            next_qty = current_qty + qty_delta
            if next_qty < 0:
                next_qty = 0.0
                qty_delta = -current_qty

        conn.execute(
            """
            UPDATE inventory_items
            SET item_name = ?,
                category = ?,
                unit = ?,
                current_quantity = ?,
                reorder_level = ?,
                default_rental_price = ?,
                active = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                name,
                (category or "General").strip() or "General",
                (unit or "pcs").strip() or "pcs",
                float(next_qty),
                float(reorder_level or 0.0),
                float(default_rental_price or 0.0),
                int(1 if active else 0),
                inv_id,
            ),
        )

        if abs(qty_delta) > 1e-9:
            conn.execute(
                """
                INSERT INTO inventory_movements (
                    inventory_item_id, movement_date, movement_type, quantity_change,
                    unit_cost, reference_invoice_id, notes
                )
                VALUES (?, ?, ?, ?, NULL, NULL, ?)
                """,
                (
                    inv_id,
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "Price List Adjustment (+/-)",
                    float(qty_delta),
                    (movement_notes or "").strip(),
                ),
            )
    return inv_id


def delete_inventory_item(item_id: int) -> None:
    inv_id = int(item_id)
    with get_connection() as conn:
        conn.execute(
            """
            DELETE FROM inventory_items
            WHERE id = ?
            """,
            (inv_id,),
        )


def inventory_item_options(active_only: bool = True) -> pd.DataFrame:
    query = """
        SELECT
            id,
            sku,
            item_name,
            category,
            unit,
            current_quantity,
            reorder_level,
            default_rental_price,
            default_unit_cost,
            unit_weight_kg,
            active
        FROM inventory_items
    """
    if active_only:
        query += " WHERE active = 1"
    query += " ORDER BY item_name ASC"
    return fetch_dataframe(query)


def add_inventory_movement(
    inventory_item_id: int,
    movement_date: str,
    movement_type: str,
    quantity_change: float,
    unit_cost: float | None = None,
    reference_invoice_id: int | None = None,
    notes: str = "",
) -> int:
    if quantity_change == 0:
        raise ValueError("Quantity change cannot be zero.")
    movement = (movement_type or "").strip()
    if not movement:
        raise ValueError("Movement type is required.")

    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO inventory_movements (
                inventory_item_id, movement_date, movement_type, quantity_change,
                unit_cost, reference_invoice_id, notes
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(inventory_item_id),
                movement_date,
                movement,
                float(quantity_change),
                None if unit_cost is None else float(unit_cost),
                reference_invoice_id,
                notes.strip(),
            ),
        )
        conn.execute(
            """
            UPDATE inventory_items
            SET current_quantity = current_quantity + ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (float(quantity_change), int(inventory_item_id)),
        )
    return int(cursor.lastrowid)


def inventory_movements(limit: int = 200) -> pd.DataFrame:
    return fetch_dataframe(
        """
        SELECT
            m.id,
            m.movement_date,
            m.movement_type,
            m.quantity_change,
            m.unit_cost,
            m.reference_invoice_id,
            m.notes,
            i.item_name,
            i.sku
        FROM inventory_movements m
        JOIN inventory_items i ON i.id = m.inventory_item_id
        ORDER BY datetime(m.movement_date) DESC, m.id DESC
        LIMIT ?
        """,
        (int(limit),),
    )


def _inventory_name_keywords(name: str) -> list[str]:
    raw = (name or "").strip().lower()
    if not raw:
        return []

    normalized = raw.replace("&", " and ").replace("×", " x ")
    normalized = re.sub(r"\bby\b", " x ", normalized)
    normalized = re.sub(r"[^a-z0-9x\s]", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if not normalized:
        return []

    tokens = [_NUMBER_WORDS.get(token, token) for token in normalized.split(" ") if token]

    def _normalize_token(token: str) -> str:
        cleaned = str(token or "").strip().lower()
        if len(cleaned) > 3 and cleaned.endswith("s") and not cleaned.endswith("ss"):
            cleaned = cleaned[:-1]
        return cleaned

    tokens = [_normalize_token(token) for token in tokens if token]

    # Collapse dimensional phrases into stable tokens (example: "10 x 10" -> "10x10").
    collapsed: list[str] = []
    idx = 0
    while idx < len(tokens):
        if (
            idx + 2 < len(tokens)
            and tokens[idx].isdigit()
            and tokens[idx + 1] == "x"
            and tokens[idx + 2].isdigit()
        ):
            collapsed.append(f"{tokens[idx]}x{tokens[idx + 2]}")
            idx += 3
            continue
        collapsed.append(tokens[idx])
        idx += 1

    return [
        token
        for token in collapsed
        if token and token != "x" and token not in _INVENTORY_NAME_STOPWORDS
    ]


def _inventory_name_signature(name: str) -> str:
    keywords = _inventory_name_keywords(name)
    if not keywords:
        return ""
    return " ".join(sorted(keywords))


def _inventory_keyword_similarity(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    inter = len(left & right)
    union = len(left | right)
    if union <= 0:
        return 0.0
    score = inter / union
    if inter >= 2 and (left.issubset(right) or right.issubset(left)):
        score = max(score, 0.9)
    return score


def _resolve_inventory_item_id(
    raw_item_name: str,
    inventory_cache: list[dict],
) -> int | None:
    signature = _inventory_name_signature(raw_item_name)
    if not signature:
        return None

    # Strongest match: normalized keyword signature.
    for row in inventory_cache:
        if row["signature"] and row["signature"] == signature:
            return int(row["id"])

    # Fallback: keyword similarity when names are close variants.
    target_keywords = set(_inventory_name_keywords(raw_item_name))
    if not target_keywords:
        return None

    best_id: int | None = None
    best_score = 0.0
    for row in inventory_cache:
        candidate_keywords = set(row["keywords"])
        score = _inventory_keyword_similarity(target_keywords, candidate_keywords)
        if score > best_score:
            best_score = score
            best_id = int(row["id"])

    if best_id is not None and best_score >= 0.68:
        return best_id
    return None


def sync_auto_invoice_inventory_movements(
    invoice_id: int,
    active: bool = True,
) -> int:
    """
    Keep auto-generated rental movement rows in sync for one invoice.

    Auto rows are stored as an OUT and RETURN pair per inventory product item.
    Pairs net to zero quantity so total stock is not permanently reduced.
    """
    inv_id = int(invoice_id)
    auto_types = ("Auto Rental Out", "Auto Rental Return")
    inserted = 0
    with get_connection() as conn:
        conn.execute(
            """
            DELETE FROM inventory_movements
            WHERE reference_invoice_id = ?
              AND movement_type IN (?, ?)
            """,
            (inv_id, auto_types[0], auto_types[1]),
        )
        if not active:
            return 0

        invoice_row = conn.execute(
            """
            SELECT
                COALESCE(event_date, '') AS event_date,
                COALESCE(event_time, '11:00') AS event_time,
                COALESCE(rental_hours, 24) AS rental_hours
            FROM invoices
            WHERE id = ?
            LIMIT 1
            """,
            (inv_id,),
        ).fetchone()
        if invoice_row is None:
            return 0

        event_date = str(invoice_row["event_date"] or "").strip()
        event_time = str(invoice_row["event_time"] or "11:00").strip() or "11:00"
        rental_hours = float(invoice_row["rental_hours"] or 24.0)
        if rental_hours <= 0:
            rental_hours = 24.0

        if event_date:
            try:
                start_dt = datetime.strptime(f"{event_date} {event_time}", "%Y-%m-%d %H:%M")
            except ValueError:
                start_dt = datetime.now()
        else:
            start_dt = datetime.now()
        end_dt = start_dt + timedelta(hours=rental_hours)

        products = conn.execute(
            """
            SELECT
                trim(COALESCE(ii.item_name, '')) AS item_name,
                COALESCE(SUM(ii.quantity), 0) AS qty,
                COALESCE(MAX(ii.unit_price), 0) AS rental_price
            FROM invoice_items ii
            WHERE ii.invoice_id = ?
              AND lower(COALESCE(ii.item_type, 'product')) = 'product'
            GROUP BY lower(trim(ii.item_name))
            """,
            (inv_id,),
        ).fetchall()

        if not products:
            return 0

        inventory_rows = conn.execute(
            """
            SELECT id, item_name
            FROM inventory_items
            """
        ).fetchall()
        inventory_cache = [
            {
                "id": int(row["id"]),
                "item_name": str(row["item_name"] or "").strip(),
                "signature": _inventory_name_signature(str(row["item_name"] or "")),
                "keywords": _inventory_name_keywords(str(row["item_name"] or "")),
            }
            for row in inventory_rows
        ]

        start_token = start_dt.strftime("%Y-%m-%d %H:%M:%S")
        end_token = end_dt.strftime("%Y-%m-%d %H:%M:%S")
        for row in products:
            item_name = str(row["item_name"] or "").strip()
            qty = float(row["qty"] or 0.0)
            if qty <= 0 or not item_name:
                continue

            matched_item_id = _resolve_inventory_item_id(item_name, inventory_cache)
            if matched_item_id is None:
                cursor = conn.execute(
                    """
                    INSERT INTO inventory_items (
                        sku, item_name, category, unit, current_quantity, reorder_level,
                        default_rental_price, default_unit_cost, unit_weight_kg, active, updated_at
                    )
                    VALUES (NULL, ?, 'General', 'pcs', 0, 0, ?, 0, 0, 1, CURRENT_TIMESTAMP)
                    """,
                    (item_name, float(row["rental_price"] or 0.0)),
                )
                inventory_item_id = int(cursor.lastrowid)
                inventory_cache.append(
                    {
                        "id": inventory_item_id,
                        "item_name": item_name,
                        "signature": _inventory_name_signature(item_name),
                        "keywords": _inventory_name_keywords(item_name),
                    }
                )
            else:
                inventory_item_id = int(matched_item_id)
                rental_price = float(row["rental_price"] or 0.0)
                conn.execute(
                    """
                    UPDATE inventory_items
                    SET default_rental_price = CASE
                            WHEN COALESCE(default_rental_price, 0) <= 0 AND ? > 0 THEN ?
                            ELSE default_rental_price
                        END,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (rental_price, rental_price, inventory_item_id),
                )
            conn.execute(
                """
                INSERT INTO inventory_movements (
                    inventory_item_id, movement_date, movement_type, quantity_change,
                    unit_cost, reference_invoice_id, notes
                )
                VALUES (?, ?, ?, ?, NULL, ?, ?)
                """,
                (
                    inventory_item_id,
                    start_token,
                    auto_types[0],
                    -qty,
                    inv_id,
                    "Auto-generated from confirmed real invoice.",
                ),
            )
            conn.execute(
                """
                INSERT INTO inventory_movements (
                    inventory_item_id, movement_date, movement_type, quantity_change,
                    unit_cost, reference_invoice_id, notes
                )
                VALUES (?, ?, ?, ?, NULL, ?, ?)
                """,
                (
                    inventory_item_id,
                    end_token,
                    auto_types[1],
                    qty,
                    inv_id,
                    "Auto-generated return based on rental duration.",
                ),
            )
            inserted += 2
    return inserted


def load_notification_log() -> pd.DataFrame:
    return fetch_dataframe(
        """
        SELECT
            invoice_id,
            notification_type,
            sent_at
        FROM event_notification_log
        """
    )


def mark_notification_sent(invoice_id: int, notification_type: str) -> None:
    reminder = (notification_type or "").strip().lower()
    if not reminder:
        raise ValueError("Notification type is required.")

    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO event_notification_log (invoice_id, notification_type, sent_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(invoice_id, notification_type) DO UPDATE SET
                sent_at = excluded.sent_at
            """,
            (int(invoice_id), reminder),
        )
