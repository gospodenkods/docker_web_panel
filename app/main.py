import os
import secrets
import time
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

import docker
from docker.errors import APIError, DockerException, ImageNotFound, NotFound
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel, Field, model_validator

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="DockPilot", version="1.0.6")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

security = HTTPBearer(auto_error=False)
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
JWT_SECRET = os.getenv("JWT_SECRET", "")
JWT_ALGORITHM = "HS256"
PANEL_USER = os.getenv("PANEL_USER", "admin")
PANEL_PASSWORD = os.getenv("PANEL_PASSWORD", "")
if not PANEL_PASSWORD or PANEL_PASSWORD.startswith("CHANGE_ME"):
    raise RuntimeError("PANEL_PASSWORD is not set or still contains a placeholder")
if not JWT_SECRET or JWT_SECRET.startswith("CHANGE_ME") or len(JWT_SECRET) < 32:
    raise RuntimeError("JWT_SECRET must be set to a random value of at least 32 characters")
if len(PANEL_PASSWORD.encode("utf-8")) > 72:
    raise RuntimeError("PANEL_PASSWORD must be no longer than 72 UTF-8 bytes")
PASSWORD_HASH = pwd_context.hash(PANEL_PASSWORD)
LOGIN_WINDOW_SECONDS = int(os.getenv("LOGIN_WINDOW_SECONDS", "300"))
LOGIN_MAX_ATTEMPTS = int(os.getenv("LOGIN_MAX_ATTEMPTS", "5"))
TRUST_PROXY_HEADERS = os.getenv("TRUST_PROXY_HEADERS", "false").lower() in {"1", "true", "yes"}
_login_attempts: dict[str, list[float]] = {}
_login_lock = threading.Lock()

def _login_key(request: Request) -> str:
    if TRUST_PROXY_HEADERS:
        forwarded = request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
        if forwarded:
            return forwarded
    return request.client.host if request.client else "unknown"

def _check_login_rate_limit(key: str) -> None:
    now = time.monotonic()
    with _login_lock:
        recent = [t for t in _login_attempts.get(key, []) if now - t < LOGIN_WINDOW_SECONDS]
        _login_attempts[key] = recent
        if len(recent) >= LOGIN_MAX_ATTEMPTS:
            raise HTTPException(429, "Слишком много попыток входа. Повторите позже")

def _record_login_failure(key: str) -> None:
    with _login_lock:
        _login_attempts.setdefault(key, []).append(time.monotonic())

def _clear_login_failures(key: str) -> None:
    with _login_lock:
        _login_attempts.pop(key, None)

def client():
    try:
        return docker.from_env(timeout=30)
    except DockerException as exc:
        raise HTTPException(503, f"Docker недоступен: {exc}")

def make_token(username: str) -> str:
    exp = datetime.now(timezone.utc) + timedelta(hours=12)
    return jwt.encode({"sub": username, "exp": exp}, JWT_SECRET, algorithm=JWT_ALGORITHM)

def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)) -> str:
    if not credentials:
        raise HTTPException(401, "Требуется авторизация")
    try:
        payload = jwt.decode(credentials.credentials, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        username = payload.get("sub")
        if username != PANEL_USER:
            raise HTTPException(401, "Недействительный токен")
        return username
    except JWTError:
        raise HTTPException(401, "Недействительный или истёкший токен")

def clean_name(name: str) -> str:
    return name.strip().lstrip("/")

def api_error(exc: Exception):
    if isinstance(exc, HTTPException):
        raise exc
    if isinstance(exc, NotFound):
        raise HTTPException(404, "Объект не найден")
    if isinstance(exc, DockerException):
        detail = getattr(exc, "explanation", None) or str(exc)
        status_code = 400 if isinstance(exc, APIError) else 503
        raise HTTPException(status_code, detail)
    raise HTTPException(500, "Внутренняя ошибка панели")

class LoginIn(BaseModel):
    username: str
    password: str

class ContainerCreate(BaseModel):
    image: str = Field(min_length=1, max_length=255)
    name: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")
    command: str | None = None
    ports: dict[str, int | str] = Field(default_factory=dict)
    environment: dict[str, str] = Field(default_factory=dict)
    volumes: dict[str, dict[str, str]] = Field(default_factory=dict)
    network: str | None = None
    restart_policy: str = Field(default="unless-stopped", pattern="^(no|always|unless-stopped|on-failure)$")
    auto_remove: bool = False

    @model_validator(mode="after")
    def validate_runtime_options(self):
        if self.auto_remove and self.restart_policy != "no":
            raise ValueError("auto_remove можно использовать только с restart_policy=no")
        return self

class NetworkCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")
    driver: Literal["bridge"] = "bridge"
    internal: bool = False
    attachable: bool = True
    subnet: str | None = None
    gateway: str | None = None

class NetworkConnect(BaseModel):
    container: str = Field(min_length=1, max_length=128)

class ImagePull(BaseModel):
    image: str = Field(min_length=1, max_length=255, pattern=r"^[^\s]+$")

class QuickDeploy(BaseModel):
    template: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9_.-]*$")
    name: str | None = Field(default=None, min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")

@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; img-src 'self' data:"
    response.headers["Cache-Control"] = "no-store"
    return response

TEMPLATES = {
    "nginx": {
        "label": "Nginx",
        "image": "nginx:alpine",
        "ports": {"80/tcp": ("127.0.0.1", 8081)},
        "description": "Лёгкий веб-сервер"
    },
    "redis": {
        "label": "Redis",
        "image": "redis:7-alpine",
        "ports": {"6379/tcp": ("127.0.0.1", 6379)},
        "generated_secret_env": "REDIS_PASSWORD",
        "description": "In-memory база данных с обязательным паролем"
    },
    "postgres": {
        "label": "PostgreSQL",
        "image": "postgres:17-alpine",
        "ports": {"5432/tcp": ("127.0.0.1", 5432)},
        "generated_secret_env": "POSTGRES_PASSWORD",
        "description": "Реляционная база данных"
    },
    "mariadb": {
        "label": "MariaDB",
        "image": "mariadb:11",
        "ports": {"3306/tcp": ("127.0.0.1", 3306)},
        "generated_secret_env": "MARIADB_ROOT_PASSWORD",
        "description": "SQL-сервер"
    },
    "uptime-kuma": {
        "label": "Uptime Kuma",
        "image": "louislam/uptime-kuma:1",
        "ports": {"3001/tcp": ("127.0.0.1", 3001)},
        "volumes": {"uptime-kuma-data": {"bind": "/app/data", "mode": "rw"}},
        "description": "Мониторинг доступности"
    }
}

@app.get("/")
def index():
    return FileResponse(str(STATIC_DIR / "index.html"))

@app.get("/api/health")
def health():
    try:
        c = client()
        c.ping()
        return {"ok": True, "docker": True}
    except Exception as exc:
        detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"ok": False, "docker": False, "error": str(detail)},
        )

@app.post("/api/login")
def login(data: LoginIn, request: Request):
    key = _login_key(request)
    _check_login_rate_limit(key)
    valid_user = secrets.compare_digest(data.username, PANEL_USER)
    valid_password = pwd_context.verify(data.password, PASSWORD_HASH)
    if not (valid_user and valid_password):
        _record_login_failure(key)
        raise HTTPException(401, "Неверный логин или пароль")
    _clear_login_failures(key)
    return {"access_token": make_token(data.username), "token_type": "bearer"}

@app.get("/api/dashboard")
def dashboard(_: str = Depends(verify_token)):
    c = client()
    try:
        info = c.info()
        containers = c.containers.list(all=True)
        images = c.images.list()
        networks = c.networks.list()
        running = sum(1 for x in containers if x.status == "running")
        return {
            "engine": {
                "name": info.get("Name"),
                "server_version": info.get("ServerVersion"),
                "os": info.get("OperatingSystem"),
                "kernel": info.get("KernelVersion"),
                "cpus": info.get("NCPU"),
                "memory": info.get("MemTotal"),
            },
            "counts": {
                "containers": len(containers),
                "running": running,
                "stopped": len(containers) - running,
                "images": len(images),
                "networks": len(networks),
            }
        }
    except Exception as exc:
        api_error(exc)

@app.get("/api/templates")
def templates(_: str = Depends(verify_token)):
    return TEMPLATES

@app.get("/api/containers")
def containers(_: str = Depends(verify_token)):
    c = client()
    result = []
    try:
        for item in c.containers.list(all=True):
            item.reload()
            attrs = item.attrs
            result.append({
                "id": item.short_id,
                "name": clean_name(item.name),
                "image": attrs.get("Config", {}).get("Image"),
                "status": item.status,
                "state": attrs.get("State", {}),
                "ports": attrs.get("NetworkSettings", {}).get("Ports", {}),
                "created": attrs.get("Created"),
                "networks": list(attrs.get("NetworkSettings", {}).get("Networks", {}).keys())
            })
        return result
    except Exception as exc:
        api_error(exc)

@app.post("/api/containers")
def create_container(data: ContainerCreate, _: str = Depends(verify_token)):
    c = client()
    try:
        try:
            c.images.get(data.image)
        except ImageNotFound:
            c.images.pull(data.image)
        restart_policy = {} if data.restart_policy == "no" else {"Name": data.restart_policy}
        container = c.containers.run(
            data.image,
            name=data.name,
            command=data.command or None,
            detach=True,
            ports=data.ports or None,
            environment=data.environment or None,
            volumes=data.volumes or None,
            network=data.network or None,
            restart_policy=restart_policy,
            auto_remove=data.auto_remove,
            labels={"dockpilot.managed": "true"}
        )
        return {"ok": True, "id": container.short_id, "name": container.name}
    except Exception as exc:
        api_error(exc)

@app.post("/api/containers/{container_id}/{action}")
def container_action(container_id: str, action: str, _: str = Depends(verify_token)):
    allowed = {"start", "stop", "restart", "pause", "unpause", "kill"}
    if action not in allowed:
        raise HTTPException(400, "Недопустимое действие")
    c = client()
    try:
        obj = c.containers.get(container_id)
        getattr(obj, action)()
        return {"ok": True}
    except Exception as exc:
        api_error(exc)

@app.delete("/api/containers/{container_id}")
def delete_container(container_id: str, force: bool = False, volumes: bool = False, _: str = Depends(verify_token)):
    c = client()
    try:
        obj = c.containers.get(container_id)
        obj.remove(force=force, v=volumes)
        return {"ok": True}
    except Exception as exc:
        api_error(exc)

@app.get("/api/containers/{container_id}/logs")
def container_logs(container_id: str, tail: int = 300, _: str = Depends(verify_token)):
    c = client()
    try:
        obj = c.containers.get(container_id)
        return {"logs": obj.logs(tail=max(1, min(tail, 2000)), timestamps=True).decode("utf-8", "replace")}
    except Exception as exc:
        api_error(exc)

@app.get("/api/containers/{container_id}/stats")
def container_stats(container_id: str, _: str = Depends(verify_token)):
    c = client()
    try:
        obj = c.containers.get(container_id)
        obj.reload()
        if obj.status != "running":
            raise HTTPException(409, "Статистика доступна только для запущенного контейнера")
        s = obj.stats(stream=False)
        cpu_delta = s["cpu_stats"]["cpu_usage"]["total_usage"] - s["precpu_stats"]["cpu_usage"].get("total_usage", 0)
        system_delta = s["cpu_stats"].get("system_cpu_usage", 0) - s["precpu_stats"].get("system_cpu_usage", 0)
        cpus = s["cpu_stats"].get("online_cpus") or len(s["cpu_stats"]["cpu_usage"].get("percpu_usage", [])) or 1
        cpu = (cpu_delta / system_delta * cpus * 100) if system_delta > 0 else 0
        mem_usage = s["memory_stats"].get("usage", 0) - s["memory_stats"].get("stats", {}).get("cache", 0)
        mem_limit = s["memory_stats"].get("limit", 0)
        return {
            "cpu_percent": round(cpu, 2),
            "memory_usage": mem_usage,
            "memory_limit": mem_limit,
            "memory_percent": round((mem_usage / mem_limit * 100), 2) if mem_limit else 0,
            "network": s.get("networks", {})
        }
    except Exception as exc:
        api_error(exc)

@app.get("/api/images")
def images(_: str = Depends(verify_token)):
    c = client()
    try:
        return [{
            "id": image.short_id,
            "tags": image.tags,
            "size": image.attrs.get("Size", 0),
            "created": image.attrs.get("Created")
        } for image in c.images.list()]
    except Exception as exc:
        api_error(exc)

@app.post("/api/images/pull")
def pull_image(payload: ImagePull, _: str = Depends(verify_token)):
    image = payload.image.strip()
    c = client()
    try:
        result = c.images.pull(image)
        return {"ok": True, "id": result.short_id, "tags": result.tags}
    except Exception as exc:
        api_error(exc)

@app.delete("/api/images/{image_id}")
def delete_image(image_id: str, force: bool = False, _: str = Depends(verify_token)):
    c = client()
    try:
        c.images.remove(image_id, force=force)
        return {"ok": True}
    except Exception as exc:
        api_error(exc)

@app.get("/api/networks")
def networks(_: str = Depends(verify_token)):
    c = client()
    try:
        result = []
        for n in c.networks.list():
            n.reload()
            result.append({
                "id": n.short_id,
                "name": n.name,
                "driver": n.attrs.get("Driver"),
                "scope": n.attrs.get("Scope"),
                "internal": n.attrs.get("Internal"),
                "attachable": n.attrs.get("Attachable"),
                "subnets": n.attrs.get("IPAM", {}).get("Config", []),
                "containers": list((n.attrs.get("Containers") or {}).values())
            })
        return result
    except Exception as exc:
        api_error(exc)

@app.post("/api/networks")
def create_network(data: NetworkCreate, _: str = Depends(verify_token)):
    c = client()
    try:
        ipam = None
        if data.subnet:
            pool = docker.types.IPAMPool(subnet=data.subnet, gateway=data.gateway)
            ipam = docker.types.IPAMConfig(pool_configs=[pool])
        n = c.networks.create(
            data.name, driver=data.driver, internal=data.internal,
            attachable=data.attachable, ipam=ipam,
            labels={"dockpilot.managed": "true"}
        )
        return {"ok": True, "id": n.short_id}
    except Exception as exc:
        api_error(exc)

@app.post("/api/networks/{network_id}/connect")
def connect_network(network_id: str, data: NetworkConnect, _: str = Depends(verify_token)):
    c = client()
    try:
        c.networks.get(network_id).connect(data.container)
        return {"ok": True}
    except Exception as exc:
        api_error(exc)

@app.post("/api/networks/{network_id}/disconnect")
def disconnect_network(network_id: str, data: NetworkConnect, force: bool = False, _: str = Depends(verify_token)):
    c = client()
    try:
        c.networks.get(network_id).disconnect(data.container, force=force)
        return {"ok": True}
    except Exception as exc:
        api_error(exc)

@app.delete("/api/networks/{network_id}")
def delete_network(network_id: str, _: str = Depends(verify_token)):
    c = client()
    try:
        c.networks.get(network_id).remove()
        return {"ok": True}
    except Exception as exc:
        api_error(exc)

@app.post("/api/quick-deploy")
def quick_deploy(data: QuickDeploy, _: str = Depends(verify_token)):
    if data.template not in TEMPLATES:
        raise HTTPException(404, "Шаблон не найден")
    cfg = TEMPLATES[data.template]
    name = data.name or f"{data.template}-{int(time.time())}"
    c = client()
    try:
        try:
            c.images.get(cfg["image"])
        except ImageNotFound:
            c.images.pull(cfg["image"])
        environment = dict(cfg.get("environment") or {})
        generated_credentials = None
        secret_env = cfg.get("generated_secret_env")
        if secret_env:
            generated_password = secrets.token_urlsafe(24)
            environment[secret_env] = generated_password
            generated_credentials = {"variable": secret_env, "password": generated_password}
        command = None
        if data.template == "redis" and generated_credentials:
            command = ["redis-server", "--requirepass", generated_credentials["password"]]
        obj = c.containers.run(
            cfg["image"], name=name, detach=True,
            command=command,
            ports=cfg.get("ports"),
            environment=environment or None,
            volumes=cfg.get("volumes"),
            restart_policy={"Name": "unless-stopped"},
            labels={"dockpilot.managed": "true", "dockpilot.template": data.template}
        )
        return {"ok": True, "id": obj.short_id, "name": obj.name, "generated_credentials": generated_credentials}
    except Exception as exc:
        api_error(exc)

@app.post("/api/prune")
def prune(payload: dict[str, Any], _: str = Depends(verify_token)):
    kind = payload.get("kind")
    c = client()
    try:
        if kind == "containers":
            return c.containers.prune()
        if kind == "images":
            return c.images.prune(filters={"dangling": True})
        if kind == "networks":
            return c.networks.prune()
        if kind == "volumes":
            return c.volumes.prune()
        raise HTTPException(400, "Недопустимый тип очистки")
    except Exception as exc:
        api_error(exc)
