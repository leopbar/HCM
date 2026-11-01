import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from fastapi import Depends, HTTPException, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import update
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import User, AccessGroup, UserSession

bearer = HTTPBearer(auto_error=False)

def _load_secret() -> bytes:
    value = os.getenv("APP_SECRET", "")
    weak = {"", "local-development-secret-change-me", "replace-this-local-development-secret"}
    if value in weak or len(value) < 32:
        if os.getenv("HCM_ALLOW_INSECURE_SECRET") == "1":  # automated tests only
            return (value or "test-only-secret").encode()
        raise RuntimeError("APP_SECRET ausente ou fraco: defina no .env um valor aleatório com pelo menos 32 caracteres.")
    return value.encode()

SECRET = _load_secret()
SESSION_COOKIE = "hcm_session"
SESSION_HOURS = int(os.getenv("SESSION_HOURS", "8"))
IDLE_MINUTES = int(os.getenv("SESSION_IDLE_MINUTES", "60"))
MAX_FAILED_LOGINS = int(os.getenv("MAX_FAILED_LOGINS", "5"))
LOCK_MINUTES = int(os.getenv("LOCK_MINUTES", "15"))
PASSWORD_MAX_AGE_DAYS = int(os.getenv("PASSWORD_MAX_AGE_DAYS", "90"))
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() == "true"
PBKDF2_ROUNDS = 310000
LEGACY_ROUNDS = 180000
# Routes an account with an expired or temporary password may still use.
PASSWORD_CHANGE_ALLOWED = ("/api/auth/me", "/api/auth/logout", "/api/access/password")

def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ROUNDS)
    return "pbkdf2$" + str(PBKDF2_ROUNDS) + "$" + base64.urlsafe_b64encode(salt + digest).decode()

def verify_password(password: str, saved: str) -> bool:
    try:
        rounds, encoded = LEGACY_ROUNDS, saved
        if saved.startswith("pbkdf2$"):
            _, rounds_text, encoded = saved.split("$", 2)
            rounds = int(rounds_text)
        raw = base64.urlsafe_b64decode(encoded.encode())
        salt, digest = raw[:16], raw[16:]
        return hmac.compare_digest(digest, hashlib.pbkdf2_hmac("sha256", password.encode(), salt, rounds))
    except (ValueError, TypeError):
        return False

def password_policy_errors(password: str, email: str = "", full_name: str = "") -> list[str]:
    errors = []
    if len(password) < 12: errors.append("A senha deve ter pelo menos 12 caracteres.")
    if not re.search(r"[a-z]", password): errors.append("Inclua ao menos uma letra minúscula.")
    if not re.search(r"[A-Z]", password): errors.append("Inclua ao menos uma letra maiúscula.")
    if not re.search(r"\d", password): errors.append("Inclua ao menos um número.")
    if not re.search(r"[^A-Za-z0-9]", password): errors.append("Inclua ao menos um caractere especial.")
    lowered = password.lower()
    local = email.split("@")[0].lower() if email else ""
    if local and len(local) >= 3 and local in lowered: errors.append("A senha não pode conter o identificador do e-mail.")
    if any(part and len(part) >= 4 and part.lower() in lowered for part in full_name.split()): errors.append("A senha não pode conter partes do nome.")
    if lowered in {"hcm123", "senha123456!", "password123!", "123456789012"}: errors.append("Senha muito comum.")
    return errors

def require_strong_password(password: str, email: str = "", full_name: str = ""):
    errors = password_policy_errors(password, email, full_name)
    if errors:
        raise HTTPException(422, " ".join(errors))

def generate_password(length: int = 20) -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
    while True:
        value = "".join(secrets.choice(alphabet) for _ in range(length - 2)) + secrets.choice("!@#$%&*?") + secrets.choice("23456789")
        if not password_policy_errors(value):
            return value

def _sign(body: str) -> str:
    return hmac.new(SECRET, body.encode(), hashlib.sha256).hexdigest()

def _encode(payload: dict) -> str:
    body = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")
    return f"{body}.{_sign(body)}"

def _decode(token: str) -> dict:
    try:
        body, signature = token.rsplit(".", 1)
        if not hmac.compare_digest(signature, _sign(body)):
            raise ValueError("invalid signature")
        payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        if int(payload["exp"]) < int(time.time()):
            raise ValueError("expired")
        return payload
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Sessão inválida ou expirada") from exc

def password_version(user: User) -> str:
    return hashlib.sha256(user.password_hash.encode()).hexdigest()[:24]

def create_session(db: Session, user: User, request: Request | None = None) -> str:
    now = datetime.now(timezone.utc)
    session = UserSession(id=secrets.token_urlsafe(32), user_id=user.id, created_at=now, last_seen_at=now, expires_at=now + timedelta(hours=SESSION_HOURS),
                          ip_address=(request.client.host if request and request.client else None))
    db.add(session)
    return _encode({"sub": user.id, "sid": session.id, "exp": int(session.expires_at.timestamp()), "pv": password_version(user)})

def set_session_cookie(response: Response, token: str):
    response.set_cookie(SESSION_COOKIE, token, httponly=True, secure=COOKIE_SECURE, samesite="strict", max_age=SESSION_HOURS * 3600, path="/")

def clear_session_cookie(response: Response):
    response.delete_cookie(SESSION_COOKIE, path="/", samesite="strict", secure=COOKIE_SECURE, httponly=True)

def revoke_user_sessions(db: Session, user_id: int, except_id: str | None = None):
    query = update(UserSession).where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
    if except_id:
        query = query.where(UserSession.id != except_id)
    db.execute(query.values(revoked_at=datetime.now(timezone.utc)))

def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value

def password_expired(user: User) -> bool:
    changed = _aware(user.password_changed_at) or _aware(user.created_at)
    return bool(PASSWORD_MAX_AGE_DAYS and changed and changed < datetime.now(timezone.utc) - timedelta(days=PASSWORD_MAX_AGE_DAYS))

def read_token(request: Request, credentials: HTTPAuthorizationCredentials | None) -> tuple[str | None, bool]:
    if credentials and credentials.credentials:
        return credentials.credentials, False
    return request.cookies.get(SESSION_COOKIE), True

def current_session(request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(bearer), db: Session = Depends(get_db)) -> tuple[User, UserSession]:
    token, from_cookie = read_token(request, credentials)
    if not token:
        raise HTTPException(status_code=401, detail="Faça login para continuar")
    # Cookie sessions must come from the application itself: the custom header forces a CORS
    # preflight, which only the configured frontend origin passes (CSRF protection).
    if from_cookie and request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get("x-requested-with") != "HCMLab":
        raise HTTPException(status_code=403, detail="Requisição sem cabeçalho de origem da aplicação")
    payload = _decode(token)
    user = db.get(User, int(payload["sub"]))
    session = db.get(UserSession, str(payload.get("sid", "")))
    now = datetime.now(timezone.utc)
    if not user or not user.active:
        raise HTTPException(status_code=401, detail="Usuário inativo ou não encontrado")
    if not session or session.user_id != user.id or session.revoked_at or _aware(session.expires_at) < now:
        raise HTTPException(status_code=401, detail="Sessão inválida ou expirada. Entre novamente.")
    if _aware(session.last_seen_at) < now - timedelta(minutes=IDLE_MINUTES):
        session.revoked_at = now; db.commit()
        raise HTTPException(status_code=401, detail="Sessão encerrada por inatividade. Entre novamente.")
    if payload.get("pv") != password_version(user):
        raise HTTPException(401, "Sessão inválida ou expirada. Entre novamente.")
    if _aware(session.last_seen_at) < now - timedelta(minutes=1):
        session.last_seen_at = now; db.commit()
    path = request.url.path
    if (user.must_change_password or password_expired(user)) and path not in PASSWORD_CHANGE_ALLOWED:
        raise HTTPException(403, {"code": "PASSWORD_CHANGE_REQUIRED", "message": "Defina uma nova senha antes de continuar."})
    if user.access_group_id:
        group = db.get(AccessGroup, user.access_group_id)
        if not group or group.company_id != user.company_id:
            raise HTTPException(403, "Grupo de acesso inválido")
        module = next((module for prefix, module in (("/api/vacations", "vacations"), ("/api/hr/timekeeping", "timekeeping"), ("/api/hr/benefits", "benefits"), ("/api/hr/admission", "admission"), ("/api/hr/payroll", "payroll"), ("/api/recruitment", "recruitment")) if path.startswith(prefix)), None)
        if path.startswith("/api/hr/configurations/"):
            module = path.rsplit("/", 1)[-1]
        if path.startswith("/api/processes"):
            module = "processes"
        if module and module not in group.modules:
            raise HTTPException(403, "Seu grupo de acesso não permite este módulo")
    return user, session

def current_user(pair: tuple[User, UserSession] = Depends(current_session)) -> User:
    return pair[0]

def require_roles(*roles: str):
    def dependency(user: User = Depends(current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status_code=403, detail="Seu perfil não tem permissão para esta operação")
        return user
    return dependency
