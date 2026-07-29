import json
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import quote, urlparse

import requests


DATA_DIR = Path(os.getenv("DOCKPILOT_DATA_DIR", "/app/data"))
BACKUP_DIR = Path(os.getenv("BACKUP_DIR", "/backups"))
SETTINGS_FILE = DATA_DIR / "backup-settings.json"
HISTORY_FILE = DATA_DIR / "backup-history.json"
_lock = threading.Lock()
_running = False

DEFAULT_SETTINGS = {
    "enabled": False,
    "interval_hours": 24,
    "target": "local",
    "local_subdir": "",
    "webdav_url": "",
    "webdav_username": "",
    "webdav_password": "",
    "webdav_path": "dockpilot",
    "last_run": None,
    "next_run": None,
}


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default.copy() if isinstance(default, dict) else list(default)


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def load_settings(include_password: bool = False) -> dict:
    settings = {**DEFAULT_SETTINGS, **_read_json(SETTINGS_FILE, DEFAULT_SETTINGS)}
    if not include_password:
        settings["webdav_password_set"] = bool(settings.get("webdav_password"))
        settings.pop("webdav_password", None)
    settings["running"] = _running
    return settings


def save_settings(data: dict) -> dict:
    current = load_settings(include_password=True)
    password = data.get("webdav_password")
    updated = {
        **current,
        "enabled": bool(data.get("enabled", False)),
        "interval_hours": max(1, min(int(data.get("interval_hours", 24)), 24 * 365)),
        "target": data.get("target", "local"),
        "local_subdir": _safe_relative(data.get("local_subdir", "")),
        "webdav_url": str(data.get("webdav_url", "")).strip().rstrip("/"),
        "webdav_username": str(data.get("webdav_username", "")).strip(),
        "webdav_password": password if password not in (None, "") else current.get("webdav_password", ""),
        "webdav_path": _safe_relative(data.get("webdav_path", "dockpilot")) or "dockpilot",
    }
    if updated["target"] not in {"local", "webdav"}:
        raise ValueError("Неизвестное хранилище")
    if updated["target"] == "webdav":
        _validate_webdav_url(updated["webdav_url"])
    if updated["enabled"]:
        updated["next_run"] = (time.time() + updated["interval_hours"] * 3600)
    else:
        updated["next_run"] = None
    _write_json(SETTINGS_FILE, updated)
    return load_settings()


def _safe_relative(value: str) -> str:
    value = str(value or "").strip().replace("\\", "/").strip("/")
    if any(part in {"", ".", ".."} for part in value.split("/") if value):
        raise ValueError("Путь должен быть относительным и не содержать '..'")
    return value


def _safe_name(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_.-]+", "-", value.strip().lstrip("/"))
    return cleaned.strip(".-")[:80] or "container"


def _auth(settings: dict):
    username = settings.get("webdav_username")
    return (username, settings.get("webdav_password", "")) if username else None


def _validate_webdav_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("WebDAV URL должен начинаться с http:// или https://")


def _webdav_collection(settings: dict) -> str:
    base = settings["webdav_url"].rstrip("/")
    path = "/".join(quote(part, safe="") for part in settings["webdav_path"].split("/"))
    return f"{base}/{path}/"


def test_webdav(settings: dict) -> dict:
    _validate_webdav_url(settings.get("webdav_url", ""))
    url = settings["webdav_url"].rstrip("/") + "/"
    response = requests.request(
        "PROPFIND", url, auth=_auth(settings), headers={"Depth": "0"}, timeout=15
    )
    if response.status_code not in {200, 207}:
        raise ValueError(f"WebDAV вернул HTTP {response.status_code}")
    return {"ok": True, "status_code": response.status_code}


def _ensure_webdav_collection(settings: dict) -> str:
    _validate_webdav_url(settings["webdav_url"])
    current = settings["webdav_url"].rstrip("/")
    for part in settings["webdav_path"].split("/"):
        current += "/" + quote(part, safe="")
        response = requests.request("MKCOL", current, auth=_auth(settings), timeout=20)
        if response.status_code not in {201, 301, 405}:
            raise RuntimeError(f"Не удалось создать WebDAV-каталог: HTTP {response.status_code}")
    return current + "/"


def _upload_webdav(path: Path, settings: dict) -> str:
    target = _ensure_webdav_collection(settings) + quote(path.name, safe="")
    with path.open("rb") as stream:
        response = requests.put(target, data=stream, auth=_auth(settings), timeout=(15, 3600))
    if response.status_code not in {200, 201, 204}:
        raise RuntimeError(f"Загрузка WebDAV завершилась HTTP {response.status_code}")
    return target


def history() -> list[dict]:
    return _read_json(HISTORY_FILE, [])


def _record(item: dict) -> None:
    records = [record for record in history() if record.get("id") != item.get("id")]
    records.insert(0, item)
    _write_json(HISTORY_FILE, records[:200])


def run_backup(docker_client, container_ids: list[str] | None = None, reason: str = "manual") -> dict:
    global _running
    if not _lock.acquire(blocking=False):
        raise RuntimeError("Резервное копирование уже выполняется")
    _running = True
    settings = load_settings(include_password=True)
    started = datetime.now(timezone.utc)
    job = {
        "id": started.strftime("%Y%m%d-%H%M%S-%f"),
        "started_at": started.isoformat(),
        "finished_at": None,
        "reason": reason,
        "target": settings["target"],
        "status": "running",
        "files": [],
        "error": None,
    }
    _record(job)
    try:
        containers = docker_client.containers.list(filters={"status": "running"})
        if container_ids:
            wanted = set(container_ids)
            containers = [c for c in containers if c.id in wanted or c.short_id in wanted or c.name in wanted]
        if not containers:
            raise RuntimeError("Не выбраны запущенные контейнеры")
        local_dir = BACKUP_DIR / settings.get("local_subdir", "")
        local_dir.mkdir(parents=True, exist_ok=True)
        for container in containers:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            base = f"{_safe_name(container.name)}-{stamp}"
            repository = f"dockpilot-backup/{_safe_name(container.name).lower()}"
            image = container.commit(repository=repository, tag=stamp, pause=True)
            archive = local_dir / f"{base}.tar"
            temporary = archive.with_suffix(".tar.part")
            try:
                with temporary.open("wb") as output:
                    for chunk in image.save(named=True):
                        output.write(chunk)
                temporary.replace(archive)
                manifest = archive.with_suffix(".json")
                container.reload()
                _write_json(manifest, {
                    "container_id": container.id,
                    "container_name": container.name,
                    "source_image": container.attrs.get("Config", {}).get("Image"),
                    "snapshot_image": image.tags,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "config": container.attrs.get("Config", {}),
                    "host_config": container.attrs.get("HostConfig", {}),
                    "mounts": container.attrs.get("Mounts", []),
                })
                locations = [str(archive), str(manifest)]
                if settings["target"] == "webdav":
                    locations = [_upload_webdav(archive, settings), _upload_webdav(manifest, settings)]
                    archive.unlink(missing_ok=True)
                    manifest.unlink(missing_ok=True)
                job["files"].append({
                    "container": container.name,
                    "size": archive.stat().st_size if archive.exists() else None,
                    "locations": locations,
                })
            finally:
                temporary.unlink(missing_ok=True)
                try:
                    docker_client.images.remove(image.id, force=True)
                except Exception:
                    pass
        job["status"] = "completed"
        return job
    except Exception as exc:
        job["status"] = "failed"
        job["error"] = str(exc)
        raise
    finally:
        try:
            job["finished_at"] = datetime.now(timezone.utc).isoformat()
            _record(job)
            settings = load_settings(include_password=True)
            settings["last_run"] = time.time()
            settings["next_run"] = (
                time.time() + settings["interval_hours"] * 3600 if settings.get("enabled") else None
            )
            _write_json(SETTINGS_FILE, settings)
        finally:
            _running = False
            _lock.release()


def start_scheduler(client_factory: Callable) -> None:
    def loop():
        while True:
            time.sleep(30)
            settings = load_settings(include_password=True)
            if settings.get("enabled") and (settings.get("next_run") or 0) <= time.time():
                try:
                    run_backup(client_factory(), reason="schedule")
                except Exception:
                    pass
    threading.Thread(target=loop, name="dockpilot-backup-scheduler", daemon=True).start()
