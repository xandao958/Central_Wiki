from __future__ import annotations

import base64
import getpass
import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import sqlite3
import sys
import time
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import Any, Generator, cast
from urllib.parse import parse_qs, urlsplit

import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
DB_PATH = Path(os.getenv("WIKI_DB_PATH", str(DATA_DIR / "wiki.db")))
COOKIE_NAME = "wiki_session"
SESSION_SECONDS = 60 * 60 * 8
ROLE_LABELS = {
    "reader": "Leitor",
    "editor": "Editor",
    "approver": "Moderador",
}
DEFAULT_APP_SETTINGS: dict[str, Any] = {
    "app_name": "Central_Wiki",
    "page_home": "Visão geral",
    "page_articles": "Base de conhecimento",
    "page_admin": "Usuários e setores",
    "page_audit": "Auditoria",
    "page_settings": "Personalizar aplicativo",
    "page_account": "Minha conta",
    "button_new_article": "Novo artigo",
    "button_save_draft": "Salvar rascunho",
    "button_submit_review": "Enviar para revisão →",
    "button_edit_article": "Editar artigo",
    "button_publish": "Aprovar e publicar",
}
STATUS_LABELS = {
    "draft": "Rascunho",
    "review": "Em revisão",
    "published": "Publicado",
}
ARTICLE_PAGE_SIZE = 50
MIN_USERNAME_LENGTH = 3
MAX_USERNAME_LENGTH = 80
MIN_PASSWORD_LENGTH = 12
MAX_SECTOR_NAME_LENGTH = 80
MIN_FULL_NAME_LENGTH = 5
MAX_FULL_NAME_LENGTH = 160
MAX_PROFILE_PHOTO_BYTES = 2 * 1024 * 1024
ARTICLE_BLOCK_FORMAT = "wiki-blocks-v1"
MAX_ARTICLE_BLOCKS = 100
MAX_ARTICLE_VIDEOS = 10
MAX_ARTICLE_CONTENT_LENGTH = 200_000
MAX_BLOCK_TEXT_LENGTH = 20_000
MAX_MEDIA_URL_LENGTH = 2_048
YOUTUBE_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


def youtube_video_id(value: str) -> str:
    if len(value) > MAX_MEDIA_URL_LENGTH:
        raise ValueError("O link do YouTube é muito longo.")
    try:
        parsed = urlsplit(value.strip())
    except ValueError as exc:
        raise ValueError("Informe um link válido do YouTube.") from exc
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
        raise ValueError("Use um link público válido do YouTube.")
    if host in {"youtu.be", "www.youtu.be"}:
        video_id = parsed.path.strip("/").split("/", maxsplit=1)[0]
    elif host in {
        "youtube.com",
        "www.youtube.com",
        "m.youtube.com",
        "youtube-nocookie.com",
        "www.youtube-nocookie.com",
    }:
        if parsed.path == "/watch":
            video_id = parse_qs(parsed.query).get("v", [""])[0]
        elif parsed.path.startswith(("/embed/", "/shorts/", "/live/")):
            video_id = parsed.path.split("/", maxsplit=2)[2].split("/", maxsplit=1)[0]
        else:
            video_id = ""
    else:
        video_id = ""
    if not YOUTUBE_VIDEO_ID.fullmatch(video_id):
        raise ValueError("Não foi possível identificar um vídeo válido do YouTube.")
    return video_id


def validate_image_url(value: str) -> str:
    url = value.strip()
    if (
        len(url) > MAX_MEDIA_URL_LENGTH
        or "\\" in url
        or any(char.isspace() or ord(char) < 32 for char in url)
    ):
        raise ValueError("O endereço da imagem é inválido ou muito longo.")
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Informe um endereço HTTPS válido para a imagem.") from exc
    normalized_host = host.lower().rstrip(".") if host else ""
    if (
        parsed.scheme.lower() != "https"
        or not host
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or normalized_host == "localhost"
        or normalized_host.endswith((".localhost", ".local"))
    ):
        raise ValueError("Imagens precisam usar um endereço HTTPS público.")
    try:
        address = ipaddress.ip_address(normalized_host)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise ValueError("O endereço da imagem precisa ser público.")
    return url


def normalize_article_content(raw_content: str) -> tuple[str, list[dict[str, str]]]:
    if len(raw_content) > MAX_ARTICLE_CONTENT_LENGTH:
        raise ValueError("O conteúdo excede o limite permitido.")
    try:
        document: Any = json.loads(raw_content)
    except (json.JSONDecodeError, TypeError):
        document = None

    parsed_document = cast(dict[str, Any], document) if isinstance(document, dict) else None
    if parsed_document is None or parsed_document.get("format") != ARTICLE_BLOCK_FORMAT:
        blocks: list[dict[str, str]] = [{"type": "paragraph", "text": raw_content.strip()}]
    else:
        supplied_blocks = parsed_document.get("blocks")
        if not isinstance(supplied_blocks, list):
            raise ValueError(f"Use no máximo {MAX_ARTICLE_BLOCKS} módulos por artigo.")
        supplied_blocks = cast(list[Any], supplied_blocks)
        if len(supplied_blocks) > MAX_ARTICLE_BLOCKS:
            raise ValueError(f"Use no máximo {MAX_ARTICLE_BLOCKS} módulos por artigo.")
        blocks = []
        video_count = 0
        for supplied in supplied_blocks:
            if not isinstance(supplied, dict):
                raise ValueError("Um dos módulos do artigo está inválido.")
            supplied = cast(dict[str, Any], supplied)
            block_type = supplied.get("type")
            if not isinstance(block_type, str):
                raise ValueError("Tipo de módulo não permitido.")
            if block_type in {"paragraph", "heading"}:
                text = supplied.get("text", "")
                if not isinstance(text, str) or len(text) > MAX_BLOCK_TEXT_LENGTH:
                    raise ValueError("O texto de um módulo excede o limite permitido.")
                text = text.strip()
                if text:
                    blocks.append({"type": block_type, "text": text})
            elif block_type == "youtube":
                url = supplied.get("url", "")
                if not isinstance(url, str):
                    raise ValueError("O link do YouTube é inválido.")
                video_id = supplied.get("video_id", "")
                if not url.strip() and not video_id:
                    continue
                video_count += 1
                if video_count > MAX_ARTICLE_VIDEOS:
                    raise ValueError(f"É permitido adicionar até {MAX_ARTICLE_VIDEOS} vídeos por artigo.")
                if video_id:
                    if not isinstance(video_id, str) or not YOUTUBE_VIDEO_ID.fullmatch(video_id):
                        raise ValueError("O identificador do vídeo do YouTube é inválido.")
                else:
                    video_id = youtube_video_id(url)
                blocks.append({"type": "youtube", "video_id": video_id})
            elif block_type == "image":
                url = supplied.get("url", "")
                alt = supplied.get("alt", "")
                if not isinstance(url, str) or not isinstance(alt, str):
                    raise ValueError("Os dados da imagem são inválidos.")
                if not url.strip() and not alt.strip():
                    continue
                if len(alt) > 300:
                    raise ValueError("A descrição da imagem deve ter até 300 caracteres.")
                if not alt.strip():
                    raise ValueError("Adicione uma descrição acessível para a imagem.")
                blocks.append(
                    {"type": "image", "url": validate_image_url(url), "alt": alt.strip()}
                )
            else:
                raise ValueError("Tipo de módulo não permitido.")
    if not any(
        (block["type"] in {"paragraph", "heading"} and block["text"])
        or block["type"] in {"youtube", "image"}
        for block in blocks
    ):
        raise ValueError("Adicione texto, uma imagem ou um vídeo ao artigo.")
    canonical = json.dumps(
        {"format": ARTICLE_BLOCK_FORMAT, "blocks": blocks},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return canonical, blocks


def article_editor_blocks(raw_content: str) -> list[dict[str, str]]:
    try:
        document: Any = json.loads(raw_content)
    except (json.JSONDecodeError, TypeError):
        document = None
    parsed_document = cast(dict[str, Any], document) if isinstance(document, dict) else None
    if parsed_document is not None and parsed_document.get("format") == ARTICLE_BLOCK_FORMAT:
        supplied_blocks = parsed_document.get("blocks")
        if isinstance(supplied_blocks, list):
            supplied_blocks = cast(list[Any], supplied_blocks)
            blocks: list[dict[str, str]] = []
            for block in supplied_blocks[:MAX_ARTICLE_BLOCKS]:
                if not isinstance(block, dict):
                    continue
                block = cast(dict[str, Any], block)
                editor_block: dict[str, str] = {
                    key: value[:MAX_BLOCK_TEXT_LENGTH]
                    if key == "text"
                    else value[:MAX_MEDIA_URL_LENGTH]
                    if key == "url"
                    else value[:300]
                    for key, value in block.items()
                    if key in {"type", "text", "url", "alt"}
                    and isinstance(value, str)
                }
                if block.get("type") == "youtube" and isinstance(block.get("video_id"), str):
                    video_id = block["video_id"]
                    if YOUTUBE_VIDEO_ID.fullmatch(video_id):
                        editor_block["url"] = f"https://www.youtube.com/watch?v={video_id}"
                blocks.append(editor_block)
            return blocks
    return [{"type": "paragraph", "text": raw_content[:MAX_BLOCK_TEXT_LENGTH]}]


def article_fallback_text(raw_content: str) -> str:
    lines: list[str] = []
    for block in article_editor_blocks(raw_content):
        if block.get("type") in {"paragraph", "heading"}:
            lines.append(block.get("text", ""))
        elif block.get("type") == "image":
            lines.append(f"[Imagem: {block.get('alt', '')}]\n{block.get('url', '')}")
        elif block.get("type") == "youtube":
            lines.append(f"[Vídeo do YouTube]\n{block.get('url', '')}")
    return "\n\n".join(line for line in lines if line).strip()[:MAX_ARTICLE_CONTENT_LENGTH]


def article_display_blocks(raw_content: str) -> list[dict[str, str]]:
    try:
        _, blocks = normalize_article_content(raw_content)
    except ValueError:
        return [{"type": "paragraph", "text": raw_content}]
    return blocks


def normalize_full_name(value: str) -> str:
    full_name = " ".join(value.split())
    if (
        len(full_name) < MIN_FULL_NAME_LENGTH
        or len(full_name) > MAX_FULL_NAME_LENGTH
        or len(full_name.split()) < 2
    ):
        raise ValueError("Informe nome e sobrenome, com até 160 caracteres.")
    return full_name


def profile_photo_type(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24 and data[12:16] == b"IHDR":
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if (
        len(data) >= 12
        and data[:4] == b"RIFF"
        and data[8:12] == b"WEBP"
    ):
        return "image/webp"
    return None


def editable_sectors(user: sqlite3.Row) -> list[sqlite3.Row]:
    with connect_db() as db:
        if user["is_admin"]:
            return db.execute("SELECT id, name FROM sectors ORDER BY name").fetchall()
        return db.execute(
            """
            SELECT s.id, s.name FROM sectors s
            JOIN memberships m ON m.sector_id = s.id
            WHERE m.user_id = ? AND m.role IN ('editor', 'approver')
            ORDER BY s.name
            """,
            (user["id"],),
        ).fetchall()


@contextmanager
def connect_db() -> Generator[sqlite3.Connection, None, None]:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize_database() -> None:
    with connect_db() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                full_name TEXT NOT NULL DEFAULT '',
                profile_photo BLOB,
                profile_photo_type TEXT,
                password_hash TEXT NOT NULL,
                is_admin INTEGER NOT NULL DEFAULT 0,
                active INTEGER NOT NULL DEFAULT 1,
                session_version INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS sectors (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL UNIQUE COLLATE NOCASE,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS memberships (
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                sector_id INTEGER NOT NULL REFERENCES sectors(id) ON DELETE CASCADE,
                role TEXT NOT NULL CHECK(role IN ('reader', 'editor', 'approver')),
                PRIMARY KEY (user_id, sector_id)
            );
            CREATE TABLE IF NOT EXISTS articles (
                id INTEGER PRIMARY KEY,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                sector_id INTEGER NOT NULL REFERENCES sectors(id),
                status TEXT NOT NULL CHECK(status IN ('draft', 'review', 'published')),
                author_id INTEGER NOT NULL REFERENCES users(id),
                version INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS article_revisions (
                id INTEGER PRIMARY KEY,
                article_id INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
                version INTEGER NOT NULL,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                status TEXT NOT NULL,
                changed_by INTEGER NOT NULL REFERENCES users(id),
                changed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY,
                user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                action TEXT NOT NULL,
                details TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS article_proposals (
                id INTEGER PRIMARY KEY,
                article_id INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
                author_id INTEGER NOT NULL REFERENCES users(id),
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('pending', 'accepted', 'rejected')),
                reviewed_by INTEGER REFERENCES users(id),
                reviewed_at TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS app_configuration (
                id INTEGER PRIMARY KEY CHECK(id = 1),
                app_name TEXT NOT NULL DEFAULT 'Central_Wiki',
                page_home TEXT NOT NULL DEFAULT 'Visão geral',
                page_articles TEXT NOT NULL DEFAULT 'Base de conhecimento',
                page_admin TEXT NOT NULL DEFAULT 'Usuários e setores',
                page_audit TEXT NOT NULL DEFAULT 'Auditoria',
                page_settings TEXT NOT NULL DEFAULT 'Personalizar aplicativo',
                page_account TEXT NOT NULL DEFAULT 'Minha conta',
                brand_version INTEGER NOT NULL DEFAULT 1,
                button_new_article TEXT NOT NULL DEFAULT 'Novo artigo',
                button_save_draft TEXT NOT NULL DEFAULT 'Salvar rascunho',
                button_submit_review TEXT NOT NULL DEFAULT 'Enviar para revisão →',
                button_edit_article TEXT NOT NULL DEFAULT 'Editar artigo',
                button_publish TEXT NOT NULL DEFAULT 'Aprovar e publicar',
                logo BLOB,
                logo_type TEXT
            );
            INSERT OR IGNORE INTO app_configuration (id) VALUES (1);
            CREATE INDEX IF NOT EXISTS idx_articles_sector_status
                ON articles(sector_id, status);
            CREATE INDEX IF NOT EXISTS idx_articles_updated_at
                ON articles(updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_audit_logs_created
                ON audit_logs(created_at);
            CREATE INDEX IF NOT EXISTS idx_article_proposals_article_status
                ON article_proposals(article_id, status, created_at);
            """
        )
        user_columns = {row["name"] for row in db.execute("PRAGMA table_info(users)")}
        if "full_name" not in user_columns:
            db.execute("ALTER TABLE users ADD COLUMN full_name TEXT NOT NULL DEFAULT ''")
        if "profile_photo" not in user_columns:
            db.execute("ALTER TABLE users ADD COLUMN profile_photo BLOB")
        if "profile_photo_type" not in user_columns:
            db.execute("ALTER TABLE users ADD COLUMN profile_photo_type TEXT")
        if "session_version" not in user_columns:
            db.execute(
                "ALTER TABLE users ADD COLUMN session_version INTEGER NOT NULL DEFAULT 1"
            )
        configuration_columns = {
            row["name"] for row in db.execute("PRAGMA table_info(app_configuration)")
        }
        configuration_migrations = {
            "page_settings": "TEXT NOT NULL DEFAULT 'Personalizar aplicativo'",
            "page_account": "TEXT NOT NULL DEFAULT 'Minha conta'",
            "brand_version": "INTEGER NOT NULL DEFAULT 1",
        }
        for column, definition in configuration_migrations.items():
            if column not in configuration_columns:
                db.execute(
                    f"ALTER TABLE app_configuration ADD COLUMN {column} {definition}"
                )
        db.execute(
            "UPDATE app_configuration SET app_name = 'Central_Wiki' "
            "WHERE app_name = 'Consat_Wiki'"
        )


def get_secret() -> bytes:
    secret_path = DATA_DIR / ".secret"
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    configured = os.getenv("WIKI_SECRET_KEY")
    if configured:
        return configured.encode("utf-8")
    if not secret_path.exists():
        secret_path.write_text(secrets.token_urlsafe(48), encoding="utf-8")
    return secret_path.read_text(encoding="utf-8").strip().encode("utf-8")


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 310_000)
    return f"{base64.urlsafe_b64encode(salt).decode()}${base64.urlsafe_b64encode(derived).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt_text, expected_text = stored.split("$", maxsplit=1)
        salt = base64.urlsafe_b64decode(salt_text)
        expected = base64.urlsafe_b64decode(expected_text)
    except (ValueError, TypeError):
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 310_000)
    return hmac.compare_digest(actual, expected)


def encode_session(
    user_id: int,
    session_version: int,
    preview_sector_id: int | None = None,
) -> tuple[str, str]:
    csrf_token = secrets.token_urlsafe(32)
    payload = json.dumps(
        {
            "uid": user_id,
            "ver": session_version,
            "csrf": csrf_token,
            "exp": int(time.time()) + SESSION_SECONDS,
            "preview_sector_id": preview_sector_id,
        },
        separators=(",", ":"),
    ).encode("utf-8")
    encoded = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    signature = hmac.new(get_secret(), encoded.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{encoded}.{signature}", csrf_token


def decode_session(token: str | None) -> dict[str, Any] | None:
    if not token or "." not in token:
        return None
    encoded, signature = token.rsplit(".", maxsplit=1)
    expected = hmac.new(get_secret(), encoded.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        padded = encoded + "=" * (-len(encoded) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
        if int(payload["exp"]) < int(time.time()):
            return None
        return payload
    except (ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None


def audit(user_id: int | None, action: str, details: str) -> None:
    with connect_db() as db:
        db.execute(
            "INSERT INTO audit_logs (user_id, action, details) VALUES (?, ?, ?)",
            (user_id, action, details[:300]),
        )


def current_user(request: Request) -> sqlite3.Row | None:
    payload = decode_session(request.cookies.get(COOKIE_NAME))
    if payload is None:
        return None
    with connect_db() as db:
        user = db.execute(
            """
            SELECT id, username, full_name, is_admin, active, session_version,
                profile_photo IS NOT NULL AS has_profile_photo
            FROM users WHERE id = ? AND active = 1
            """,
            (payload["uid"],),
        ).fetchone()
    if user is None or payload.get("ver") != user["session_version"]:
        return None
    return user


def require_user(request: Request) -> sqlite3.Row:
    user = current_user(request)
    if user is None:
        raise HTTPException(status_code=401)
    return user


def preview_sector_for(request: Request, user: sqlite3.Row) -> int | None:
    if not user["is_admin"]:
        return None
    payload = decode_session(request.cookies.get(COOKIE_NAME))
    preview_sector_id = payload.get("preview_sector_id") if payload else None
    return int(preview_sector_id) if preview_sector_id is not None else None


def csrf_from_request(request: Request) -> str | None:
    payload = decode_session(request.cookies.get(COOKIE_NAME))
    return payload.get("csrf") if payload else None


def validate_csrf(request: Request, submitted: str) -> sqlite3.Row:
    user = require_user(request)
    expected = csrf_from_request(request)
    if not expected or not hmac.compare_digest(expected, submitted):
        raise HTTPException(status_code=403, detail="Token de segurança inválido.")
    if (
        preview_sector_for(request, user) is not None
        and request.url.path not in {"/admin/preview", "/logout"}
    ):
        raise HTTPException(
            status_code=403,
            detail="O modo de visualização é somente leitura. Encerre-o para realizar alterações.",
        )
    return user


def require_not_preview(request: Request, user: sqlite3.Row) -> None:
    if preview_sector_for(request, user) is not None:
        raise HTTPException(
            status_code=403,
            detail="Encerre o modo de visualização para acessar esta área.",
        )


def memberships_for(user_id: int) -> list[sqlite3.Row]:
    with connect_db() as db:
        return db.execute(
            """
            SELECT s.id, s.name, m.role
            FROM memberships m JOIN sectors s ON s.id = m.sector_id
            WHERE m.user_id = ? ORDER BY s.name
            """,
            (user_id,),
        ).fetchall()


def app_configuration() -> dict[str, Any]:
    with connect_db() as db:
        row = db.execute(
            """
            SELECT app_name, page_home, page_articles, page_admin, page_audit,
                page_settings, page_account, button_new_article, button_save_draft,
                button_submit_review, button_edit_article, button_publish, brand_version,
                logo IS NOT NULL AS has_logo, logo_type
            FROM app_configuration WHERE id = 1
            """
        ).fetchone()
    configuration: dict[str, Any] = dict(DEFAULT_APP_SETTINGS)
    if row:
        configuration.update(
            {key: row[key] for key in DEFAULT_APP_SETTINGS if row[key]}
        )
        configuration["has_logo"] = bool(row["has_logo"])
        configuration["logo_type"] = row["logo_type"]
        configuration["brand_version"] = row["brand_version"]
    else:
        configuration["has_logo"] = False
        configuration["logo_type"] = None
        configuration["brand_version"] = 1
    return configuration


def can_access_sector(user: sqlite3.Row, sector_id: int) -> bool:
    if user["is_admin"]:
        return True
    with connect_db() as db:
        return (
            db.execute(
                "SELECT 1 FROM memberships WHERE user_id = ? AND sector_id = ?",
                (user["id"], sector_id),
            ).fetchone()
            is not None
        )


def role_for(user_id: int, sector_id: int) -> str | None:
    with connect_db() as db:
        row = db.execute(
            "SELECT role FROM memberships WHERE user_id = ? AND sector_id = ?",
            (user_id, sector_id),
        ).fetchone()
    return row["role"] if row else None


def can_edit(user: sqlite3.Row, sector_id: int) -> bool:
    if user["is_admin"]:
        return True
    return role_for(user["id"], sector_id) in {"editor", "approver"}


def can_review_proposals(user: sqlite3.Row, sector_id: int) -> bool:
    return bool(user["is_admin"]) or role_for(user["id"], sector_id) == "approver"


def page_context(request: Request, user: sqlite3.Row | None = None, **extra: Any) -> dict[str, Any]:
    payload = decode_session(request.cookies.get(COOKIE_NAME))
    preview_sector_id = preview_sector_for(request, user) if user else None
    memberships: list[Any] = memberships_for(user["id"]) if user else []
    display_user: sqlite3.Row | dict[str, Any] | None = user
    if preview_sector_id is not None and user:
        with connect_db() as db:
            sector = db.execute(
                "SELECT id, name FROM sectors WHERE id = ?",
                (preview_sector_id,),
            ).fetchone()
        memberships = (
            [{"id": sector["id"], "name": sector["name"], "role": "reader"}]
            if sector
            else []
        )
        display_user = {
            "id": user["id"],
            "username": user["username"],
            "full_name": user["full_name"],
            "is_admin": 0,
            "has_profile_photo": user["has_profile_photo"],
        }
    sidebar_access_label = (
        "TI · Administrador"
        if display_user and display_user["is_admin"]
        else " · ".join(
            f"{ROLE_LABELS.get(membership['role'], 'Colaborador')} · {membership['name']}"
            for membership in memberships
        )
        or "Sem setor vinculado"
    )
    context: dict[str, Any] = {
        "request": request,
        "user": display_user,
        "csrf_token": payload.get("csrf", "") if payload else "",
        "memberships": memberships,
        "preview_active": preview_sector_id is not None,
        "preview_sector_id": preview_sector_id,
        "role_labels": ROLE_LABELS,
        "status_labels": STATUS_LABELS,
        "sidebar_full_name": (
            (
                (display_user["full_name"] or "").strip()
                or display_user["username"]
            )
            if display_user
            else ""
        ),
        "sidebar_access_label": sidebar_access_label,
        "app_configuration": app_configuration(),
    }
    context.update(extra)
    return context


@asynccontextmanager
async def lifespan(_: FastAPI):
    initialize_database()
    yield


app = FastAPI(title="Central_Wiki", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
templates = Jinja2Templates(directory=ROOT / "templates")


@app.exception_handler(401)
async def unauthorized(_request: Request, _: HTTPException) -> RedirectResponse:
    return RedirectResponse("/login", status_code=303)


@app.exception_handler(403)
async def forbidden(request: Request, exc: HTTPException) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="error.html",
        context=page_context(request, current_user(request), message=str(exc.detail)),
        status_code=403,
    )


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    with connect_db() as db:
        has_users = db.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={
            "request": request,
            "has_users": has_users,
            "error": "",
            "app_configuration": app_configuration(),
        },
    )


@app.post("/login")
async def login(request: Request, username: str = Form(), password: str = Form()):
    with connect_db() as db:
        user = db.execute(
            "SELECT * FROM users WHERE username = ? AND active = 1",
            (username.strip(),),
        ).fetchone()
    if user is None or not verify_password(password, user["password_hash"]):
        audit(user["id"] if user else None, "login_failed", f"Login inválido: {username[:80]}")
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={
                "request": request,
                "has_users": True,
                "error": "Usuário ou senha inválidos.",
                "app_configuration": app_configuration(),
            },
            status_code=400,
        )
    token, _ = encode_session(user["id"], user["session_version"])
    audit(user["id"], "login", "Acesso autenticado")
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(
        COOKIE_NAME,
        token,
        httponly=True,
        secure=os.getenv("WIKI_COOKIE_SECURE", "0") == "1",
        samesite="lax",
        max_age=SESSION_SECONDS,
        path="/",
    )
    return response


@app.post("/logout")
async def logout(request: Request, csrf_token: str = Form(default="")):
    user = validate_csrf(request, csrf_token)
    audit(user["id"], "logout", "Sessão encerrada")
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(COOKIE_NAME, path="/")
    return response


@app.get("/account/password", response_class=HTMLResponse)
async def change_password_page(request: Request, error: str = ""):
    user = require_user(request)
    require_not_preview(request, user)
    return templates.TemplateResponse(
        request=request,
        name="password.html",
        context=page_context(request, user, error=error),
    )


@app.post("/account/profile")
async def update_account_profile(
    request: Request,
    csrf_token: str = Form(default=""),
    photo: UploadFile | None = File(default=None),
    remove_photo: str = Form(default=""),
):
    user = validate_csrf(request, csrf_token)
    if remove_photo not in {"", "yes"}:
        raise HTTPException(status_code=400, detail="Opção de foto inválida.")
    if photo is not None and photo.filename:
        photo_data = await photo.read(MAX_PROFILE_PHOTO_BYTES + 1)
        await photo.close()
        if len(photo_data) > MAX_PROFILE_PHOTO_BYTES:
            return RedirectResponse("/account/password?error=photo-size", status_code=303)
        photo_type = profile_photo_type(photo_data)
        if photo_type is None:
            return RedirectResponse("/account/password?error=photo-type", status_code=303)
    else:
        photo_data = None
        photo_type = None
    with connect_db() as db:
        if remove_photo == "yes":
            db.execute(
                """
                UPDATE users SET profile_photo = NULL, profile_photo_type = NULL
                WHERE id = ?
                """,
                (user["id"],),
            )
        elif photo_data is not None:
            db.execute(
                """
                UPDATE users SET profile_photo = ?, profile_photo_type = ?
                WHERE id = ?
                """,
                (photo_data, photo_type, user["id"]),
            )
        else:
            return RedirectResponse("/account/password?updated=password-only", status_code=303)
    audit(user["id"], "profile_updated", "Foto de exibição atualizada")
    return RedirectResponse("/account/password?updated=profile", status_code=303)


@app.get("/account/profile/photo")
async def account_profile_photo(request: Request):
    user = require_user(request)
    with connect_db() as db:
        account = db.execute(
            "SELECT profile_photo, profile_photo_type FROM users WHERE id = ?",
            (user["id"],),
        ).fetchone()
    if account is None or account["profile_photo"] is None or account["profile_photo_type"] is None:
        raise HTTPException(status_code=404, detail="Foto de perfil não encontrada.")
    return Response(
        content=account["profile_photo"],
        media_type=account["profile_photo_type"],
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": "inline",
        },
    )


@app.post("/account/password")
async def change_password(
    request: Request,
    csrf_token: str = Form(default=""),
    current_password: str = Form(),
    new_password: str = Form(),
    confirmation: str = Form(),
):
    user = validate_csrf(request, csrf_token)
    with connect_db() as db:
        account = db.execute(
            "SELECT password_hash, session_version FROM users WHERE id = ?",
            (user["id"],),
        ).fetchone()
    if not verify_password(current_password, account["password_hash"]):
        return RedirectResponse("/account/password?error=current", status_code=303)
    if len(new_password) < MIN_PASSWORD_LENGTH:
        return RedirectResponse("/account/password?error=length", status_code=303)
    if new_password != confirmation:
        return RedirectResponse("/account/password?error=confirm", status_code=303)
    new_version = account["session_version"] + 1
    with connect_db() as db:
        db.execute(
            "UPDATE users SET password_hash = ?, session_version = ? WHERE id = ?",
            (hash_password(new_password), new_version, user["id"]),
        )
    audit(user["id"], "password_changed", "Senha da conta alterada")
    token, _ = encode_session(user["id"], new_version)
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(
        COOKIE_NAME,
        token,
        httponly=True,
        secure=os.getenv("WIKI_COOKIE_SECURE", "0") == "1",
        samesite="lax",
        max_age=SESSION_SECONDS,
        path="/",
    )
    return response


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    user = require_user(request)
    preview_sector_id = preview_sector_for(request, user)
    sectors = memberships_for(user["id"])
    sector_ids = [sector["id"] for sector in sectors]
    articles: list[sqlite3.Row] = []
    pending_count = 0
    if preview_sector_id is not None:
        with connect_db() as db:
            articles = db.execute(
                """
                SELECT a.*, s.name AS sector_name, u.username AS author_name
                FROM articles a JOIN sectors s ON s.id = a.sector_id
                JOIN users u ON u.id = a.author_id
                WHERE a.sector_id = ? AND a.status = 'published'
                ORDER BY a.updated_at DESC LIMIT 8
                """,
                (preview_sector_id,),
            ).fetchall()
    elif user["is_admin"]:
        with connect_db() as db:
            articles = db.execute(
                """
                SELECT a.*, s.name AS sector_name, u.username AS author_name
                FROM articles a JOIN sectors s ON s.id = a.sector_id
                JOIN users u ON u.id = a.author_id
                ORDER BY a.updated_at DESC LIMIT 8
                """
            ).fetchall()
            pending_count = db.execute(
                "SELECT COUNT(*) FROM articles WHERE status = 'review'"
            ).fetchone()[0]
    elif sector_ids:
        placeholders = ",".join("?" for _ in sector_ids)
        with connect_db() as db:
            articles = db.execute(
                f"""
                SELECT a.*, s.name AS sector_name, u.username AS author_name
                FROM articles a JOIN sectors s ON s.id = a.sector_id
                JOIN users u ON u.id = a.author_id
                WHERE a.sector_id IN ({placeholders})
                  AND (a.status = 'published' OR a.author_id = ? OR EXISTS (
                    SELECT 1 FROM memberships m
                    WHERE m.user_id = ? AND m.sector_id = a.sector_id AND m.role = 'approver'
                  ))
                ORDER BY a.updated_at DESC LIMIT 8
                """,
                (*sector_ids, user["id"], user["id"]),
            ).fetchall()
            pending_count = db.execute(
                f"SELECT COUNT(*) FROM articles WHERE status = 'review' AND sector_id IN ({placeholders})",
                sector_ids,
            ).fetchone()[0]
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context=page_context(request, user, articles=articles, pending_count=pending_count),
    )


@app.get("/articles", response_class=HTMLResponse)
async def article_list(
    request: Request,
    q: str = "",
    sector_id: int | None = None,
    page: int = Query(default=1, ge=1),
):
    user = require_user(request)
    preview_sector_id = preview_sector_for(request, user)
    with connect_db() as db:
        query = """
            FROM articles a JOIN sectors s ON s.id = a.sector_id
            JOIN users u ON u.id = a.author_id
        """
        params: list[Any] = []
        if preview_sector_id is not None:
            query += """
                WHERE a.sector_id = ? AND a.status = 'published'
                  AND (? = '' OR a.title LIKE ? OR a.content LIKE ?)
            """
            params = [
                preview_sector_id,
                q.strip(),
                f"%{q.strip()}%",
                f"%{q.strip()}%",
            ]
        elif user["is_admin"]:
            query += " WHERE (? = '' OR a.title LIKE ? OR a.content LIKE ?)"
            params: list[Any] = [q.strip(), f"%{q.strip()}%", f"%{q.strip()}%"]
            if sector_id:
                query += " AND a.sector_id = ?"
                params.append(sector_id)
        else:
            user_sectors = memberships_for(user["id"])
            ids = [sector["id"] for sector in user_sectors]
            if not ids:
                articles = []
                return templates.TemplateResponse(
                    request=request,
                    name="articles.html",
                    context=page_context(
                        request,
                        user,
                        articles=articles,
                        sectors=user_sectors,
                        q=q,
                        sector_id=sector_id,
                        page=page,
                        total_pages=1,
                        total_count=0,
                    ),
                )
            placeholders = ",".join("?" for _ in ids)
            query += f"""
                WHERE a.sector_id IN ({placeholders})
                  AND (a.status = 'published' OR a.author_id = ? OR EXISTS (
                    SELECT 1 FROM memberships m
                    WHERE m.user_id = ? AND m.sector_id = a.sector_id AND m.role = 'approver'
                  ))
                  AND (? = '' OR a.title LIKE ? OR a.content LIKE ?)
            """
            params = [*ids, user["id"], user["id"], q.strip(), f"%{q.strip()}%", f"%{q.strip()}%"]
            if sector_id:
                query += " AND a.sector_id = ?"
                params.append(sector_id)
        total_count = db.execute("SELECT COUNT(*) " + query, params).fetchone()[0]
        articles = db.execute(
            """
            SELECT a.id, a.title, a.sector_id, a.status, a.version, a.created_at,
                a.updated_at, s.name AS sector_name, u.username AS author_name
            """
            + query
            + " ORDER BY a.updated_at DESC, a.id DESC LIMIT ? OFFSET ?",
            (*params, ARTICLE_PAGE_SIZE, (page - 1) * ARTICLE_PAGE_SIZE),
        ).fetchall()
        if preview_sector_id is not None:
            sectors = db.execute(
                "SELECT id, name FROM sectors WHERE id = ?",
                (preview_sector_id,),
            ).fetchall()
        else:
            sectors = db.execute("SELECT id, name FROM sectors ORDER BY name").fetchall() if user["is_admin"] else memberships_for(user["id"])
    total_pages = max(1, (total_count + ARTICLE_PAGE_SIZE - 1) // ARTICLE_PAGE_SIZE)
    return templates.TemplateResponse(
        request=request,
        name="articles.html",
        context=page_context(
            request,
            user,
            articles=articles,
            sectors=sectors,
            q=q,
            sector_id=sector_id,
            page=page,
            total_pages=total_pages,
            total_count=total_count,
        ),
    )


@app.get("/articles/new", response_class=HTMLResponse)
async def new_article_page(request: Request):
    user = require_user(request)
    require_not_preview(request, user)
    sectors = editable_sectors(user)
    return templates.TemplateResponse(
        request=request,
        name="article_form.html",
        context=page_context(
            request,
            user,
            article=None,
            sectors=sectors,
            error="",
            editor_blocks=[],
            editor_title="",
            editor_sector_id=None,
            editor_fallback_content="",
            max_article_videos=MAX_ARTICLE_VIDEOS,
            max_article_blocks=MAX_ARTICLE_BLOCKS,
        ),
    )


@app.post("/articles/new")
async def create_article(
    request: Request,
    csrf_token: str = Form(default=""),
    title: str = Form(),
    content: str = Form(),
    sector_id: int = Form(),
    action: str = Form("draft"),
):
    user = validate_csrf(request, csrf_token)
    if not can_edit(user, sector_id):
        raise HTTPException(status_code=403, detail="Você não tem permissão para editar este setor.")
    title = title.strip()
    try:
        stored_content, blocks = normalize_article_content(content)
    except ValueError as exc:
        stored_content = ""
        blocks = article_editor_blocks(content)
        error = str(exc)
    else:
        error = ""
    if not title or len(title) > 180:
        error = "Informe um título com até 180 caracteres."
    if action not in {"draft", "review"}:
        raise HTTPException(status_code=400, detail="Ação de artigo inválida.")
    if error:
        return templates.TemplateResponse(
            request=request,
            name="article_form.html",
            context=page_context(
                request,
                user,
                article=None,
                sectors=editable_sectors(user),
                error=error,
                editor_blocks=blocks,
                editor_title=title[:180],
                editor_sector_id=sector_id,
                editor_fallback_content=article_fallback_text(content),
                max_article_videos=MAX_ARTICLE_VIDEOS,
                max_article_blocks=MAX_ARTICLE_BLOCKS,
            ),
            status_code=400,
        )
    status = "review" if action == "review" else "draft"
    with connect_db() as db:
        cursor = db.execute(
            "INSERT INTO articles (title, content, sector_id, status, author_id) VALUES (?, ?, ?, ?, ?)",
            (title, stored_content, sector_id, status, user["id"]),
        )
        article_id = cursor.lastrowid
        db.execute(
            """
            INSERT INTO article_revisions (article_id, version, title, content, status, changed_by)
            VALUES (?, 1, ?, ?, ?, ?)
            """,
            (article_id, title, stored_content, status, user["id"]),
        )
    audit(user["id"], "article_created", f"Artigo {article_id} criado ({status})")
    return RedirectResponse(f"/articles/{article_id}", status_code=303)


@app.get("/articles/{article_id}", response_class=HTMLResponse)
async def article_detail(request: Request, article_id: int):
    user = require_user(request)
    preview_sector_id = preview_sector_for(request, user)
    with connect_db() as db:
        article = db.execute(
            """
            SELECT a.*, s.name AS sector_name, u.username AS author_name
            FROM articles a JOIN sectors s ON s.id = a.sector_id
            JOIN users u ON u.id = a.author_id WHERE a.id = ?
            """,
            (article_id,),
        ).fetchone()
    if article is None:
        raise HTTPException(status_code=404, detail="Artigo não encontrado.")
    if preview_sector_id is not None and (
        article["sector_id"] != preview_sector_id or article["status"] != "published"
    ):
        raise HTTPException(status_code=404, detail="Artigo não encontrado.")
    if not can_access_sector(user, article["sector_id"]):
        raise HTTPException(status_code=403, detail="Você não tem acesso a este setor.")
    if article["status"] != "published" and not (
        user["is_admin"]
        or article["author_id"] == user["id"]
        or role_for(user["id"], article["sector_id"]) == "approver"
    ):
        raise HTTPException(status_code=403, detail="Este artigo ainda não foi publicado.")
    can_suggest = can_edit(user, article["sector_id"])
    can_review = can_review_proposals(user, article["sector_id"])
    with connect_db() as db:
        revisions = db.execute(
            """
            SELECT r.*, u.username AS editor_name
            FROM article_revisions r JOIN users u ON u.id = r.changed_by
            WHERE r.article_id = ? ORDER BY r.version DESC
            """,
            (article_id,),
        ).fetchall()
        proposal_rows: list[sqlite3.Row] = db.execute(
            """
            SELECT p.*, u.username AS author_name, reviewer.username AS reviewer_name
            FROM article_proposals p
            JOIN users u ON u.id = p.author_id
            LEFT JOIN users reviewer ON reviewer.id = p.reviewed_by
            WHERE p.article_id = ? ORDER BY p.created_at DESC, p.id DESC
            """,
            (article_id,),
        ).fetchall()
    proposals: list[dict[str, Any]] = (
        [
            {**dict(proposal), "blocks": article_display_blocks(proposal["content"])}
            for proposal in proposal_rows
        ]
        if can_suggest or can_review
        else []
    )
    return templates.TemplateResponse(
        request=request,
        name="article_detail.html",
        context=page_context(
            request,
            user,
            article=article,
            article_blocks=article_display_blocks(article["content"]),
            revisions=revisions,
            can_edit=can_edit(user, article["sector_id"]),
            can_suggest=can_suggest,
            can_review_proposals=can_review,
            proposals=proposals,
        ),
    )


@app.post("/articles/{article_id}/proposals")
async def submit_article_proposal(
    request: Request,
    article_id: int,
    csrf_token: str = Form(default=""),
    title: str = Form(),
    content: str = Form(),
):
    user = validate_csrf(request, csrf_token)
    with connect_db() as db:
        article = db.execute(
            "SELECT id, sector_id FROM articles WHERE id = ?",
            (article_id,),
        ).fetchone()
    if article is None:
        raise HTTPException(status_code=404, detail="Artigo não encontrado.")
    if not can_access_sector(user, article["sector_id"]) or not can_edit(user, article["sector_id"]):
        raise HTTPException(status_code=403, detail="Você não pode sugerir alterações neste setor.")
    title = title.strip()
    if not title or len(title) > 180:
        return RedirectResponse(f"/articles/{article_id}?proposal_error=title", status_code=303)
    if not content.strip():
        return RedirectResponse(f"/articles/{article_id}?proposal_error=content", status_code=303)
    try:
        stored_content, _ = normalize_article_content(content)
    except ValueError:
        return RedirectResponse(f"/articles/{article_id}?proposal_error=content", status_code=303)
    with connect_db() as db:
        db.execute(
            """
            INSERT INTO article_proposals (article_id, author_id, title, content, status)
            VALUES (?, ?, ?, ?, 'pending')
            """,
            (article_id, user["id"], title, stored_content),
        )
    audit(user["id"], "article_proposal_submitted", f"Proposta enviada para artigo {article_id}")
    return RedirectResponse(f"/articles/{article_id}?proposal=sent", status_code=303)


@app.post("/articles/{article_id}/proposals/{proposal_id}")
async def review_article_proposal(
    request: Request,
    article_id: int,
    proposal_id: int,
    csrf_token: str = Form(default=""),
    action: str = Form(),
):
    user = validate_csrf(request, csrf_token)
    if action not in {"accept", "reject"}:
        raise HTTPException(status_code=400, detail="Decisão de proposta inválida.")
    try:
        with connect_db() as db:
            article = db.execute(
                "SELECT * FROM articles WHERE id = ?",
                (article_id,),
            ).fetchone()
            proposal = db.execute(
                "SELECT * FROM article_proposals WHERE id = ? AND article_id = ?",
                (proposal_id, article_id),
            ).fetchone()
            if article is None or proposal is None:
                raise HTTPException(status_code=404, detail="Artigo ou proposta não encontrado.")
            if not can_review_proposals(user, article["sector_id"]):
                raise HTTPException(status_code=403, detail="Somente Moderadores do setor ou TI podem revisar propostas.")
            if proposal["status"] != "pending":
                raise HTTPException(status_code=409, detail="Esta proposta já foi analisada.")
            if action == "accept":
                _, article_blocks = normalize_article_content(article["content"])
                _, proposal_blocks = normalize_article_content(proposal["content"])
                proposal_blocks: list[dict[str, str]]
                proposal_blocks = [{"type": "heading", "text": proposal["title"]}, *proposal_blocks]
                combined = json.dumps(
                    {"format": ARTICLE_BLOCK_FORMAT, "blocks": article_blocks + proposal_blocks},
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                stored_content, _ = normalize_article_content(combined)
                new_version = article["version"] + 1
                new_status = "review" if article["status"] == "published" else article["status"]
                db.execute(
                    """
                    INSERT INTO article_revisions (article_id, version, title, content, status, changed_by)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        article_id,
                        new_version,
                        article["title"],
                        stored_content,
                        new_status,
                        user["id"],
                    ),
                )
                updated = db.execute(
                    """
                    UPDATE articles SET content = ?, status = ?, version = ?,
                        updated_at = CURRENT_TIMESTAMP WHERE id = ? AND version = ?
                    """,
                    (
                        stored_content,
                        new_status,
                        new_version,
                        article_id,
                        article["version"],
                    ),
                )
                if updated.rowcount != 1:
                    raise HTTPException(status_code=409, detail="O artigo mudou durante a integração. Recarregue e tente novamente.")
            reviewed = db.execute(
                """
                UPDATE article_proposals SET status = ?, reviewed_by = ?,
                    reviewed_at = CURRENT_TIMESTAMP WHERE id = ? AND status = 'pending'
                """,
                ("accepted" if action == "accept" else "rejected", user["id"], proposal_id),
            )
            if reviewed.rowcount != 1:
                raise HTTPException(status_code=409, detail="Esta proposta já foi analisada.")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit(
        user["id"],
        "article_proposal_reviewed",
        f"Proposta {proposal_id} {'integrada' if action == 'accept' else 'rejeitada'} no artigo {article_id}",
    )
    return RedirectResponse(f"/articles/{article_id}?proposal={action}", status_code=303)


@app.get("/articles/{article_id}/edit", response_class=HTMLResponse)
async def edit_article_page(request: Request, article_id: int):
    user = require_user(request)
    require_not_preview(request, user)
    with connect_db() as db:
        article = db.execute("SELECT * FROM articles WHERE id = ?", (article_id,)).fetchone()
        sectors = db.execute("SELECT id, name FROM sectors ORDER BY name").fetchall() if user["is_admin"] else []
    if article is None:
        raise HTTPException(status_code=404, detail="Artigo não encontrado.")
    if not can_edit(user, article["sector_id"]):
        raise HTTPException(status_code=403, detail="Você não tem permissão para editar este artigo.")
    if not user["is_admin"]:
        sectors = memberships_for(user["id"])
    return templates.TemplateResponse(
        request=request,
        name="article_form.html",
        context=page_context(
            request,
            user,
            article=article,
            sectors=sectors,
            error="",
            editor_blocks=article_editor_blocks(article["content"]),
            editor_title=article["title"],
            editor_sector_id=article["sector_id"],
            editor_fallback_content=article_fallback_text(article["content"]),
            max_article_videos=MAX_ARTICLE_VIDEOS,
            max_article_blocks=MAX_ARTICLE_BLOCKS,
        ),
    )


@app.post("/articles/{article_id}/edit")
async def edit_article(
    request: Request,
    article_id: int,
    csrf_token: str = Form(default=""),
    title: str = Form(),
    content: str = Form(),
    sector_id: int = Form(),
    version: int | None = Form(default=None),
    action: str = Form("draft"),
):
    user = validate_csrf(request, csrf_token)
    with connect_db() as db:
        existing = db.execute("SELECT * FROM articles WHERE id = ?", (article_id,)).fetchone()
    if existing is None:
        raise HTTPException(status_code=404, detail="Artigo não encontrado.")
    if not can_edit(user, existing["sector_id"]) or not can_edit(user, sector_id):
        raise HTTPException(status_code=403, detail="Você não tem permissão para alterar este artigo.")
    if version is None or version != existing["version"]:
        raise HTTPException(
            status_code=409,
            detail="Este artigo foi alterado desde que você o abriu. Recarregue a página e integre suas mudanças como proposta.",
        )
    title = title.strip()
    try:
        stored_content, blocks = normalize_article_content(content)
    except ValueError as exc:
        stored_content = ""
        blocks = article_editor_blocks(content)
        error = str(exc)
    else:
        error = ""
    if not title or len(title) > 180:
        error = "Informe um título com até 180 caracteres."
    if action not in {"draft", "review"}:
        raise HTTPException(status_code=400, detail="Ação de artigo inválida.")
    if error:
        sectors = editable_sectors(user)
        return templates.TemplateResponse(
            request=request,
            name="article_form.html",
            context=page_context(
                request,
                user,
                article=existing,
                sectors=sectors,
                error=error,
                editor_blocks=blocks,
                editor_title=title[:180],
                editor_sector_id=sector_id,
                editor_fallback_content=article_fallback_text(content),
                max_article_videos=MAX_ARTICLE_VIDEOS,
                max_article_blocks=MAX_ARTICLE_BLOCKS,
            ),
            status_code=400,
        )
    new_status = (
        "review"
        if action == "review" or existing["status"] == "published"
        else existing["status"]
    )
    new_version = existing["version"] + 1
    with connect_db() as db:
        db.execute(
            """
            INSERT INTO article_revisions (article_id, version, title, content, status, changed_by)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (article_id, new_version, title, stored_content, new_status, user["id"]),
        )
        updated = db.execute(
            """
            UPDATE articles SET title = ?, content = ?, sector_id = ?, status = ?,
                version = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND version = ?
            """,
            (title, stored_content, sector_id, new_status, new_version, article_id, version),
        )
        if updated.rowcount != 1:
            raise HTTPException(
                status_code=409,
                detail="Este artigo foi alterado durante o salvamento. Recarregue a página e integre suas mudanças como proposta.",
            )
    audit(user["id"], "article_updated", f"Artigo {article_id} atualizado para versão {new_version}")
    return RedirectResponse(f"/articles/{article_id}", status_code=303)


@app.post("/articles/{article_id}/publish")
async def publish_article(request: Request, article_id: int, csrf_token: str = Form(default="")):
    user = validate_csrf(request, csrf_token)
    with connect_db() as db:
        article = db.execute("SELECT * FROM articles WHERE id = ?", (article_id,)).fetchone()
    if article is None:
        raise HTTPException(status_code=404, detail="Artigo não encontrado.")
    if not user["is_admin"] and role_for(user["id"], article["sector_id"]) != "approver":
        raise HTTPException(status_code=403, detail="Somente um Moderador do setor pode publicar.")
    if article["status"] != "review":
        raise HTTPException(status_code=409, detail="Somente artigos em revisão podem ser publicados.")
    with connect_db() as db:
        db.execute(
            "UPDATE articles SET status = 'published', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (article_id,),
        )
        db.execute(
            """
            INSERT INTO article_revisions (article_id, version, title, content, status, changed_by)
            VALUES (?, ?, ?, ?, 'published', ?)
            """,
            (article_id, article["version"], article["title"], article["content"], user["id"]),
        )
    audit(user["id"], "article_published", f"Artigo {article_id} publicado")
    return RedirectResponse(f"/articles/{article_id}", status_code=303)


@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    user = require_user(request)
    require_not_preview(request, user)
    if not user["is_admin"]:
        raise HTTPException(status_code=403, detail="Área exclusiva para administradores.")
    with connect_db() as db:
        users = db.execute("SELECT id, username, active, is_admin FROM users ORDER BY username").fetchall()
        sectors = db.execute("SELECT id, name FROM sectors ORDER BY name").fetchall()
        memberships = db.execute(
            """
            SELECT m.user_id, m.sector_id, m.role, s.name AS sector_name
            FROM memberships m JOIN sectors s ON s.id = m.sector_id
            """
        ).fetchall()
    return templates.TemplateResponse(
        request=request,
        name="admin.html",
        context=page_context(
            request,
            user,
            users=users,
            sectors=sectors,
            memberships=memberships,
            error="",
        ),
    )


@app.post("/admin/sectors")
async def create_sector(request: Request, csrf_token: str = Form(default=""), name: str = Form()):
    user = validate_csrf(request, csrf_token)
    if not user["is_admin"]:
        raise HTTPException(status_code=403, detail="Área exclusiva para administradores.")
    name = name.strip()
    if not name or len(name) > MAX_SECTOR_NAME_LENGTH:
        return RedirectResponse("/admin?error=sector-validation", status_code=303)
    with connect_db() as db:
        result = db.execute(
            "INSERT INTO sectors (name) VALUES (?) ON CONFLICT(name) DO NOTHING",
            (name,),
        )
    if result.rowcount == 0:
        return RedirectResponse("/admin?error=duplicate-sector", status_code=303)
    audit(user["id"], "sector_created", f"Setor criado: {name}")
    return RedirectResponse("/admin", status_code=303)


@app.get("/admin/settings", response_class=HTMLResponse)
async def admin_settings_page(request: Request):
    user = require_user(request)
    require_not_preview(request, user)
    if not user["is_admin"]:
        raise HTTPException(status_code=403, detail="Área exclusiva para administradores.")
    return templates.TemplateResponse(
        request=request,
        name="admin_settings.html",
        context=page_context(
            request,
            user,
            settings=app_configuration(),
            error=request.query_params.get("error", ""),
        ),
    )


@app.post("/admin/settings")
async def update_admin_settings(
    request: Request,
    csrf_token: str = Form(default=""),
    app_name: str = Form(default=""),
    page_home: str = Form(default=""),
    page_articles: str = Form(default=""),
    page_admin: str = Form(default=""),
    page_audit: str = Form(default=""),
    page_settings: str = Form(default=""),
    page_account: str = Form(default=""),
    button_new_article: str = Form(default=""),
    button_save_draft: str = Form(default=""),
    button_submit_review: str = Form(default=""),
    button_edit_article: str = Form(default=""),
    button_publish: str = Form(default=""),
    logo: UploadFile | None = File(default=None),
    remove_logo: str = Form(default=""),
):
    user = validate_csrf(request, csrf_token)
    if not user["is_admin"]:
        raise HTTPException(status_code=403, detail="Área exclusiva para administradores.")
    values = {
        "app_name": app_name,
        "page_home": page_home,
        "page_articles": page_articles,
        "page_admin": page_admin,
        "page_audit": page_audit,
        "page_settings": page_settings,
        "page_account": page_account,
        "button_new_article": button_new_article,
        "button_save_draft": button_save_draft,
        "button_submit_review": button_submit_review,
        "button_edit_article": button_edit_article,
        "button_publish": button_publish,
    }
    values = {key: value.strip() for key, value in values.items()}
    if any(
        not value
        or len(value) > 80
        or any(ord(character) < 32 for character in value)
        for value in values.values()
    ):
        return RedirectResponse("/admin/settings?error=labels", status_code=303)
    if remove_logo not in {"", "yes"}:
        raise HTTPException(status_code=400, detail="Opção de logo inválida.")
    logo_data: bytes | None = None
    logo_type: str | None = None
    if logo is not None and logo.filename:
        logo_data = await logo.read(MAX_PROFILE_PHOTO_BYTES + 1)
        await logo.close()
        if len(logo_data) > MAX_PROFILE_PHOTO_BYTES:
            return RedirectResponse("/admin/settings?error=logo-size", status_code=303)
        logo_type = profile_photo_type(logo_data)
        if logo_type is None:
            return RedirectResponse("/admin/settings?error=logo-type", status_code=303)
    columns = ", ".join(f"{key} = ?" for key in values)
    parameters: list[Any] = list(values.values())
    if remove_logo == "yes":
        columns += ", logo = NULL, logo_type = NULL"
    elif logo_data is not None:
        columns += ", logo = ?, logo_type = ?"
        parameters.extend((logo_data, logo_type))
    with connect_db() as db:
        db.execute(
            f"UPDATE app_configuration SET {columns}, brand_version = brand_version + 1 WHERE id = 1",
            parameters,
        )
    audit(user["id"], "app_configuration_updated", "Marca e rótulos da aplicação atualizados")
    return RedirectResponse("/admin/settings?updated=yes", status_code=303)


@app.get("/branding/logo")
async def app_logo():
    with connect_db() as db:
        row = db.execute(
            "SELECT logo, logo_type FROM app_configuration WHERE id = 1"
        ).fetchone()
    if row is None or row["logo"] is None or row["logo_type"] is None:
        raise HTTPException(status_code=404, detail="Logo não configurado.")
    return Response(
        content=row["logo"],
        media_type=row["logo_type"],
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": "inline",
        },
    )


@app.get("/favicon.ico")
async def favicon():
    return await app_logo()


@app.post("/admin/users")
async def create_user(
    request: Request,
    csrf_token: str = Form(default=""),
    username: str = Form(),
    password: str = Form(),
    is_admin: str = Form(""),
    full_name: str = Form(),
    sector_id: str = Form(""),
    role: str = Form(""),
):
    user = validate_csrf(request, csrf_token)
    if not user["is_admin"]:
        raise HTTPException(status_code=403, detail="Área exclusiva para administradores.")
    username = username.strip()
    if (
        not MIN_USERNAME_LENGTH <= len(username) <= MAX_USERNAME_LENGTH
        or len(password) < MIN_PASSWORD_LENGTH
    ):
        return RedirectResponse("/admin?error=user-validation", status_code=303)
    if is_admin not in {"", "yes"}:
        raise HTTPException(status_code=400, detail="Opção de administrador inválida.")
    try:
        full_name = normalize_full_name(full_name)
    except ValueError:
        return RedirectResponse("/admin?error=full-name", status_code=303)
    selected_sector_id: int | None = None
    if is_admin != "yes" and sector_id:
        try:
            selected_sector_id = int(sector_id)
        except ValueError:
            return RedirectResponse("/admin?error=sector-selection", status_code=303)
        if role not in ROLE_LABELS:
            return RedirectResponse("/admin?error=role-selection", status_code=303)
    with connect_db() as db:
        result = db.execute(
            """
            INSERT INTO users (username, full_name, password_hash, is_admin) VALUES (?, ?, ?, ?)
            ON CONFLICT(username) DO NOTHING
            """,
            (username, full_name, hash_password(password), int(is_admin == "yes")),
        )
        if result.rowcount and selected_sector_id is not None:
            if db.execute(
                "SELECT 1 FROM sectors WHERE id = ?",
                (selected_sector_id,),
            ).fetchone() is None:
                raise HTTPException(status_code=404, detail="Setor não encontrado.")
            db.execute(
                "INSERT INTO memberships (user_id, sector_id, role) VALUES (?, ?, ?)",
                (result.lastrowid, selected_sector_id, role),
            )
    if result.rowcount == 0:
        return RedirectResponse("/admin?error=duplicate-user", status_code=303)
    audit(
        user["id"],
        "user_created",
        f"Usuário criado: {username}"
        + (
            f", setor {selected_sector_id}, função {ROLE_LABELS[role]}"
            if selected_sector_id is not None
            else ""
        ),
    )
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/memberships")
async def update_membership(
    request: Request,
    csrf_token: str = Form(default=""),
    user_id: int = Form(),
    sector_id: int = Form(),
    role: str = Form(),
):
    actor = validate_csrf(request, csrf_token)
    if not actor["is_admin"]:
        raise HTTPException(status_code=403, detail="Área exclusiva para administradores.")
    with connect_db() as db:
        target = db.execute(
            "SELECT is_admin FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        if target is None:
            raise HTTPException(status_code=404, detail="Usuário não encontrado.")
        if target["is_admin"] and role != "none":
            raise HTTPException(
                status_code=400,
                detail="Contas administradoras não podem ser vinculadas a setores.",
            )
        if db.execute("SELECT 1 FROM sectors WHERE id = ?", (sector_id,)).fetchone() is None:
            raise HTTPException(status_code=404, detail="Setor não encontrado.")
        if role == "none":
            db.execute(
                "DELETE FROM memberships WHERE user_id = ? AND sector_id = ?",
                (user_id, sector_id),
            )
        elif role in ROLE_LABELS:
            db.execute(
                """
                INSERT INTO memberships (user_id, sector_id, role) VALUES (?, ?, ?)
                ON CONFLICT(user_id, sector_id) DO UPDATE SET role = excluded.role
                """,
                (user_id, sector_id, role),
            )
        else:
            raise HTTPException(status_code=400, detail="Função inválida.")
    action = "membership_removed" if role == "none" else "membership_updated"
    audit(actor["id"], action, f"Vínculo alterado: usuário {user_id}, setor {sector_id}")
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/users/{user_id}/toggle")
async def toggle_user(request: Request, user_id: int, csrf_token: str = Form(default="")):
    actor = validate_csrf(request, csrf_token)
    if not actor["is_admin"]:
        raise HTTPException(status_code=403, detail="Área exclusiva para administradores.")
    if actor["id"] == user_id:
        raise HTTPException(status_code=400, detail="Não é possível desativar sua própria conta.")
    with connect_db() as db:
        target = db.execute("SELECT active, username FROM users WHERE id = ?", (user_id,)).fetchone()
        if target is None:
            raise HTTPException(status_code=404, detail="Usuário não encontrado.")
        db.execute(
            "UPDATE users SET active = ?, session_version = session_version + 1 WHERE id = ?",
            (0 if target["active"] else 1, user_id),
        )
    audit(actor["id"], "user_status_changed", f"Estado de {target['username']} alterado")
    return RedirectResponse("/admin", status_code=303)


@app.get("/admin/audit", response_class=HTMLResponse)
async def audit_page(request: Request):
    user = require_user(request)
    require_not_preview(request, user)
    if not user["is_admin"]:
        raise HTTPException(status_code=403, detail="Área exclusiva para administradores.")
    with connect_db() as db:
        logs = db.execute(
            """
            SELECT a.*, u.username FROM audit_logs a
            LEFT JOIN users u ON u.id = a.user_id
            ORDER BY a.id DESC LIMIT 200
            """
        ).fetchall()
    return templates.TemplateResponse(
        request=request,
        name="audit.html",
        context=page_context(request, user, logs=logs),
    )


@app.get("/api/v1/sectors")
async def api_sectors(request: Request):
    user = require_user(request)
    preview_sector_id = preview_sector_for(request, user)
    if preview_sector_id is not None:
        with connect_db() as db:
            sector = db.execute(
                "SELECT id, name FROM sectors WHERE id = ?",
                (preview_sector_id,),
            ).fetchone()
        return [dict(sector)] if sector else []
    sectors = memberships_for(user["id"])
    if user["is_admin"]:
        with connect_db() as db:
            sectors = db.execute("SELECT id, name FROM sectors ORDER BY name").fetchall()
    return [dict(sector) for sector in sectors]


@app.get("/api/v1/articles")
async def api_articles(
    request: Request,
    q: str = "",
    limit: int = Query(default=100, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> list[dict[str, Any]]:
    user = require_user(request)
    preview_sector_id = preview_sector_for(request, user)
    rows: list[sqlite3.Row] = []
    with connect_db() as db:
        if preview_sector_id is not None:
            rows = db.execute(
                """
                SELECT id, title, sector_id, status, version, updated_at
                FROM articles
                WHERE sector_id = ? AND status = 'published'
                  AND (? = '' OR title LIKE ?)
                ORDER BY updated_at DESC, id DESC LIMIT ? OFFSET ?
                """,
                (
                    preview_sector_id,
                    q.strip(),
                    f"%{q.strip()}%",
                    limit,
                    offset,
                ),
            ).fetchall()
        elif user["is_admin"]:
            rows = db.execute(
                """
                SELECT id, title, sector_id, status, version, updated_at
                FROM articles
                WHERE (? = '' OR title LIKE ?)
                ORDER BY updated_at DESC, id DESC LIMIT ? OFFSET ?
                """,
                (q.strip(), f"%{q.strip()}%", limit, offset),
            ).fetchall()
        else:
            ids = [sector["id"] for sector in memberships_for(user["id"])]
            if not ids:
                return []
            placeholders = ",".join("?" for _ in ids)
            rows = db.execute(
                f"""
                SELECT id, title, sector_id, status, version, updated_at
                FROM articles
                WHERE sector_id IN ({placeholders})
                  AND (status = 'published' OR author_id = ? OR EXISTS (
                    SELECT 1 FROM memberships m
                    WHERE m.user_id = ? AND m.sector_id = articles.sector_id AND m.role = 'approver'
                  ))
                  AND (? = '' OR title LIKE ?)
                ORDER BY updated_at DESC, id DESC LIMIT ? OFFSET ?
                """,
                (*ids, user["id"], user["id"], q.strip(), f"%{q.strip()}%", limit, offset),
            ).fetchall()
    return [dict(row) for row in rows]


@app.post("/admin/preview")
async def toggle_preview_mode(
    request: Request,
    csrf_token: str = Form(default=""),
    action: str = Form(),
    sector_id: int | None = Form(default=None),
):
    user = validate_csrf(request, csrf_token)
    if not user["is_admin"]:
        raise HTTPException(status_code=403, detail="Área exclusiva para administradores.")

    selected_sector_id = None
    if action == "start":
        if sector_id is None:
            raise HTTPException(status_code=400, detail="Selecione um setor para visualizar.")
        with connect_db() as db:
            sector = db.execute(
                "SELECT id FROM sectors WHERE id = ?",
                (sector_id,),
            ).fetchone()
        if sector is None:
            raise HTTPException(status_code=404, detail="Setor não encontrado.")
        selected_sector_id = sector_id
        audit(user["id"], "reader_preview_started", f"Visualização de leitor no setor {sector_id}")
    elif action == "stop":
        audit(user["id"], "reader_preview_stopped", "Visualização de leitor encerrada")
    else:
        raise HTTPException(status_code=400, detail="Ação de visualização inválida.")

    token, _ = encode_session(
        user["id"],
        user["session_version"],
        preview_sector_id=selected_sector_id,
    )
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(
        COOKIE_NAME,
        token,
        httponly=True,
        secure=os.getenv("WIKI_COOKIE_SECURE", "0") == "1",
        samesite="lax",
        max_age=SESSION_SECONDS,
        path="/",
    )
    return response


def create_initial_admin() -> None:
    initialize_database()
    with connect_db() as db:
        if db.execute("SELECT 1 FROM users WHERE is_admin = 1 LIMIT 1").fetchone():
            print("Já existe uma conta administradora.")
            return
    username = input("Usuário administrador: ").strip()
    password = getpass.getpass("Senha (mínimo de 12 caracteres): ")
    confirmation = getpass.getpass("Confirme a senha: ")
    if (
        not MIN_USERNAME_LENGTH <= len(username) <= MAX_USERNAME_LENGTH
        or len(password) < MIN_PASSWORD_LENGTH
    ):
        raise SystemExit(
            "Usuário deve ter entre 3 e 80 caracteres e a senha, ao menos 12."
        )
    if password != confirmation:
        raise SystemExit("As senhas não conferem.")
    with connect_db() as db:
        db.execute(
            "INSERT INTO users (username, password_hash, is_admin) VALUES (?, ?, 1)",
            (username, hash_password(password)),
        )
    print("Conta administradora criada. Inicie o servidor com: python main.py")


def check_database_integrity() -> None:
    if not DB_PATH.exists():
        print("Banco de dados ainda não inicializado; será criado na primeira inicialização.")
        return
    try:
        with sqlite3.connect(DB_PATH) as db:
            result = db.execute("PRAGMA quick_check").fetchone()
            if result is None or result[0] != "ok":
                raise SystemExit(f"Falha na verificação do banco de dados: {result[0] if result else 'sem resultado'}.")
            foreign_key_errors = db.execute("PRAGMA foreign_key_check").fetchall()
            if foreign_key_errors:
                details = "; ".join(f"{row[0]} rowid={row[1]}" for row in foreign_key_errors[:10])
                raise SystemExit(f"O banco contém referências inválidas: {details}.")
    except sqlite3.DatabaseError as exc:
        raise SystemExit(f"Não foi possível verificar o banco de dados: {exc}") from exc
    print("Integridade do banco de dados: OK.")


def configured_server_port() -> int:
    value = os.getenv("WIKI_PORT", "8443")
    try:
        port = int(value)
    except ValueError as exc:
        raise SystemExit("WIKI_PORT precisa ser um número entre 1 e 65535.") from exc
    if not 1 <= port <= 65535:
        raise SystemExit("WIKI_PORT precisa ser um número entre 1 e 65535.")
    return port


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "create-admin":
        create_initial_admin()
    elif len(sys.argv) > 1 and sys.argv[1] == "app-name":
        initialize_database()
        print(app_configuration()["app_name"])
    elif len(sys.argv) > 1 and sys.argv[1] == "app-brand":
        initialize_database()
        configuration = app_configuration()
        print(
            json.dumps(
                {
                    "name": configuration["app_name"],
                    "version": configuration["brand_version"],
                    "has_logo": configuration["has_logo"],
                },
                ensure_ascii=True,
            )
        )
    elif len(sys.argv) > 1 and sys.argv[1] == "app-logo":
        initialize_database()
        with connect_db() as db:
            logo = db.execute(
                "SELECT logo FROM app_configuration WHERE id = 1"
            ).fetchone()["logo"]
        if logo is None:
            raise SystemExit("O logo do aplicativo não está configurado.")
        print(base64.b64encode(logo).decode("ascii"))
    elif len(sys.argv) > 1 and sys.argv[1] == "check":
        check_database_integrity()
    else:
        uvicorn.run(
            "main:app",
            host=os.getenv("WIKI_HOST", "127.0.0.1"),
            port=configured_server_port(),
            reload=os.getenv("WIKI_RELOAD", "0") == "1",
        )
