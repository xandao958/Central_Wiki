import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

import main


def test_configured_server_port_defaults_to_51600_and_accepts_override(monkeypatch):
    monkeypatch.delenv("WIKI_PORT", raising=False)
    assert main.configured_server_port() == 51600

    monkeypatch.setenv("WIKI_PORT", "12345")
    assert main.configured_server_port() == 12345


@pytest.mark.parametrize("value", ["not-a-port", "0", "65536"])
def test_configured_server_port_rejects_invalid_values(monkeypatch, value):
    monkeypatch.setenv("WIKI_PORT", value)
    with pytest.raises(SystemExit, match="WIKI_PORT"):
        main.configured_server_port()


@pytest.fixture
def app_client(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    monkeypatch.setattr(main, "DATA_DIR", data_dir)
    monkeypatch.setattr(main, "DB_PATH", data_dir / "wiki.db")
    with TestClient(main.app) as client:
        with main.connect_db() as db:
            db.execute(
                "INSERT INTO users (username, password_hash, is_admin) VALUES (?, ?, ?)",
                ("admin", main.hash_password("admin-password-123"), 1),
            )
            db.execute(
                "INSERT INTO users (username, password_hash) VALUES (?, ?)",
                ("editor", main.hash_password("editor-password-123")),
            )
            db.execute("INSERT INTO sectors (name) VALUES ('Monitoramento')")
            db.execute("INSERT INTO sectors (name) VALUES ('Treinamento')")
            db.execute("INSERT INTO sectors (name) VALUES ('Financeiro')")
            db.execute(
                "INSERT INTO memberships (user_id, sector_id, role) VALUES (2, 1, 'editor')"
            )
            db.execute(
                "INSERT INTO memberships (user_id, sector_id, role) VALUES (2, 2, 'reader')"
            )
            db.execute(
                """
                INSERT INTO articles (title, content, sector_id, status, author_id)
                VALUES ('Procedimento', 'Texto publicado', 1, 'published', 2)
                """
            )
            db.execute(
                """
                INSERT INTO articles (title, content, sector_id, status, author_id)
                VALUES ('Material restrito', 'Não deve aparecer', 3, 'published', 1)
                """
            )
        yield client


def sign_in(client: TestClient, username: str = "editor", password: str = "editor-password-123"):
    response = client.post(
        "/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )
    assert response.status_code == 303
    return main.decode_session(client.cookies.get(main.COOKIE_NAME))["csrf"]


def test_login_and_reject_invalid_credentials(app_client):
    response = app_client.post(
        "/login",
        data={"username": "editor", "password": "wrong-password"},
    )
    assert response.status_code == 400
    csrf = sign_in(app_client)
    assert app_client.get("/").status_code == 200
    assert csrf


def test_login_and_authenticated_pages_include_theme_controls(app_client):
    login_page = app_client.get("/login")
    assert login_page.status_code == 200
    assert "data-theme-toggle" in login_page.text
    assert "/static/app.js" in login_page.text

    sign_in(app_client, "admin", "admin-password-123")
    dashboard = app_client.get("/")
    assert dashboard.status_code == 200
    assert "data-theme-toggle" in dashboard.text


def test_protected_sidebar_shows_full_name_role_and_sectors_only_in_target_card(app_client):
    with main.connect_db() as db:
        db.execute(
            "UPDATE users SET full_name = 'Alex Ferreira' WHERE username = 'editor'"
        )
    csrf = sign_in(app_client)
    dashboard = app_client.get("/")
    assert dashboard.status_code == 200
    assert "Ambiente protegido" in dashboard.text
    assert 'class="brand-logo-placeholder"' in dashboard.text
    assert 'rel="icon" href="data:,"' in dashboard.text
    assert "Acesso por setor" in dashboard.text
    assert '<strong title="Alex Ferreira">Alex Ferreira</strong>' in dashboard.text
    assert "Editor · Monitoramento · Leitor · Treinamento" in dashboard.text
    assert '<div class="sidebar-profile-avatar">' in dashboard.text
    assert "SISTEMA" not in dashboard.text
    assert dashboard.text.index('class="sidebar-footer"') < dashboard.text.index('class="sidebar-security"')
    assert dashboard.text.index('class="sidebar-security"') < dashboard.text.index('class="sidebar-bottom"')
    assert dashboard.text.index('class="sidebar-bottom"') < dashboard.text.index('class="sidebar-tool-list"')
    assert dashboard.text.index('action="/logout"') < dashboard.text.index('data-theme-toggle')
    assert dashboard.text.index('data-theme-toggle') < dashboard.text.index('href="/account/password"')

    response = app_client.post(
        "/account/profile",
        data={"csrf_token": csrf, "full_name": "Nome Alterado"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/account/password?updated=password-only"

    dashboard = app_client.get("/")
    assert '<strong title="Alex Ferreira">Alex Ferreira</strong>' in dashboard.text
    assert "Editor · Monitoramento · Leitor · Treinamento" in dashboard.text
    assert 'class="sidebar-profile-avatar"' in dashboard.text
    assert 'href="/account/password#profile"' in dashboard.text
    assert 'aria-label="Ícones do sistema e configurações"' in dashboard.text
    assert 'href="/account/password"' in dashboard.text
    assert 'action="/logout"' in dashboard.text
    assert "<span>Tema</span>" in dashboard.text
    assert "<span>Minha conta</span>" in dashboard.text
    assert "<span>Sair</span>" in dashboard.text


def test_profile_name_is_not_editable_by_the_user(app_client):
    with main.connect_db() as db:
        db.execute("UPDATE users SET full_name = 'Alex Ferreira' WHERE username = 'editor'")
    csrf = sign_in(app_client)
    response = app_client.post(
        "/account/profile",
        data={"csrf_token": csrf, "full_name": "Alex"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/account/password?updated=password-only"

    with main.connect_db() as db:
        assert db.execute(
            "SELECT full_name FROM users WHERE username = 'editor'"
        ).fetchone()["full_name"] == "Alex Ferreira"


def test_user_can_upload_and_remove_safe_profile_photo(app_client):
    with main.connect_db() as db:
        db.execute("UPDATE users SET full_name = 'Alex Ferreira' WHERE username = 'editor'")
    csrf = sign_in(app_client)
    png = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + b"\x00\x00\x00\x01\x00\x00\x00\x01"
    response = app_client.post(
        "/account/profile",
        data={"csrf_token": csrf},
        files={"photo": ("profile.png", png, "image/png")},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/account/password?updated=profile"

    dashboard = app_client.get("/")
    assert '<img src="/account/profile/photo" alt="Foto de Alex Ferreira">' in dashboard.text
    photo = app_client.get("/account/profile/photo")
    assert photo.status_code == 200
    assert photo.content == png
    assert photo.headers["content-type"] == "image/png"
    assert photo.headers["x-content-type-options"] == "nosniff"
    assert photo.headers["cache-control"] == "private, no-store"

    response = app_client.post(
        "/account/profile",
        data={"csrf_token": csrf, "remove_photo": "yes"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert app_client.get("/account/profile/photo").status_code == 404
    assert 'class="sidebar-profile-avatar"' in app_client.get("/").text


@pytest.mark.parametrize(
    ("username", "password", "role"),
    [
        ("admin", "admin-password-123", None),
        ("editor", "editor-password-123", "editor"),
        ("reader", "reader-password-123", "reader"),
        ("moderator", "moderator-password-123", "approver"),
    ],
)
def test_every_role_can_update_own_password_and_profile_photo(app_client, username, password, role):
    if role in {"reader", "approver"}:
        with main.connect_db() as db:
            user_id = db.execute(
                """
                INSERT INTO users (username, full_name, password_hash)
                VALUES (?, ?, ?)
                RETURNING id
                """,
                (username, f"Pessoa {username}", main.hash_password(password)),
            ).fetchone()["id"]
            db.execute(
                "INSERT INTO memberships (user_id, sector_id, role) VALUES (?, 1, ?)",
                (user_id, role),
            )

    csrf = sign_in(app_client, username, password)
    account_page = app_client.get("/account/password")
    assert account_page.status_code == 200
    assert "atualizar ou remover sua própria foto" in account_page.text
    png = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + b"\x00\x00\x00\x01\x00\x00\x00\x01"
    photo_response = app_client.post(
        "/account/profile",
        data={"csrf_token": csrf, "user_id": "1"},
        files={"photo": ("profile.png", png, "image/png")},
        follow_redirects=False,
    )
    assert photo_response.status_code == 303
    assert app_client.get("/account/profile/photo").content == png

    new_password = "updated-password-456"
    password_response = app_client.post(
        "/account/password",
        data={
            "csrf_token": csrf,
            "current_password": password,
            "new_password": new_password,
            "confirmation": new_password,
        },
        follow_redirects=False,
    )
    assert password_response.status_code == 303

    with main.connect_db() as db:
        account = db.execute(
            """
            SELECT id, password_hash, profile_photo
            FROM users WHERE username = ?
            """,
            (username,),
        ).fetchone()
        assert main.verify_password(new_password, account["password_hash"])
        assert account["profile_photo"] == png
        if username != "admin":
            admin = db.execute(
                "SELECT profile_photo FROM users WHERE username = 'admin'"
            ).fetchone()
            assert admin["profile_photo"] is None


def test_profile_photo_upload_rejects_active_formats_and_oversized_files(app_client):
    csrf = sign_in(app_client)
    invalid_photo = b"<svg xmlns='http://www.w3.org/2000/svg'></svg>"
    response = app_client.post(
        "/account/profile",
        data={"csrf_token": csrf, "full_name": "Alex Ferreira"},
        files={"photo": ("profile.svg", invalid_photo, "image/svg+xml")},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/account/password?error=photo-type"

    response = app_client.post(
        "/account/profile",
        data={"csrf_token": csrf, "full_name": "Alex Ferreira"},
        files={
            "photo": (
                "large.png",
                b"\x89PNG\r\n\x1a\n" + b"x" * (main.MAX_PROFILE_PHOTO_BYTES + 1),
                "image/png",
            )
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/account/password?error=photo-size"
    with main.connect_db() as db:
        assert db.execute(
            "SELECT profile_photo FROM users WHERE username = 'editor'"
        ).fetchone()["profile_photo"] is None

    csrf = sign_in(app_client)
    valid_jpeg = b"\xff\xd8\xff\xe0valid-jpeg-data"
    assert app_client.post(
        "/account/profile",
        data={"csrf_token": csrf, "full_name": "Alex Ferreira"},
        files={"photo": ("profile.jpg", valid_jpeg, "image/jpeg")},
        follow_redirects=False,
    ).status_code == 303
    response = app_client.post(
        "/account/profile",
        data={"csrf_token": csrf, "full_name": "Alex Ferreira"},
        files={"photo": ("profile.svg", invalid_photo, "image/svg+xml")},
        follow_redirects=False,
    )
    assert response.headers["location"] == "/account/password?error=photo-type"
    photo = app_client.get("/account/profile/photo")
    assert photo.content == valid_jpeg
    assert photo.headers["content-type"] == "image/jpeg"


def test_admin_can_create_account_with_existing_sector_and_role(app_client):
    csrf = sign_in(app_client, "admin", "admin-password-123")
    response = app_client.post(
        "/admin/users",
        data={
            "csrf_token": csrf,
            "username": "moderator",
            "full_name": "Maria Silva",
            "password": "moderator-password-123",
            "sector_id": "1",
            "role": "approver",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    with main.connect_db() as db:
        membership = db.execute(
            """
            SELECT s.name, m.role FROM memberships m
            JOIN sectors s ON s.id = m.sector_id
            JOIN users u ON u.id = m.user_id WHERE u.username = 'moderator'
            """
        ).fetchone()
    assert tuple(membership) == ("Monitoramento", "approver")


def test_moderator_access_is_limited_to_assigned_sectors(app_client):
    admin_csrf = sign_in(app_client, "admin", "admin-password-123")
    created = app_client.post(
        "/admin/users",
        data={
            "csrf_token": admin_csrf,
            "username": "moderator",
            "full_name": "Maria Silva",
            "password": "moderator-password-123",
            "sector_id": "1",
            "role": "approver",
        },
        follow_redirects=False,
    )
    assert created.status_code == 303
    admin_page = app_client.get("/admin")
    assert "TI · Administrador" in admin_page.text
    assert "Monitoramento — Editor" in admin_page.text
    assert ">Moderador</option>" in admin_page.text
    sign_in(app_client, "moderator", "moderator-password-123")
    assert app_client.get("/articles/1/edit").status_code == 200
    assert app_client.get("/articles/2/edit").status_code == 403


def test_editor_proposal_requires_assigned_moderator_to_integrate(app_client):
    admin_csrf = sign_in(app_client, "admin", "admin-password-123")
    created = app_client.post(
        "/admin/users",
        data={
            "csrf_token": admin_csrf,
            "username": "moderator",
            "full_name": "Maria Silva",
            "password": "moderator-password-123",
            "sector_id": "1",
            "role": "approver",
        },
        follow_redirects=False,
    )
    assert created.status_code == 303

    editor_csrf = sign_in(app_client)
    submitted = app_client.post(
        "/articles/1/proposals",
        data={
            "csrf_token": editor_csrf,
            "title": "Etapa adicional",
            "content": "Verifique o painel antes da operação.",
        },
        follow_redirects=False,
    )
    assert submitted.status_code == 303
    assert "proposal=sent" in submitted.headers["location"]

    editor_review = app_client.post(
        "/articles/1/proposals/1",
        data={"csrf_token": editor_csrf, "action": "accept"},
    )
    assert editor_review.status_code == 403

    moderator_csrf = sign_in(
        app_client,
        "moderator",
        "moderator-password-123",
    )
    integrated = app_client.post(
        "/articles/1/proposals/1",
        data={"csrf_token": moderator_csrf, "action": "accept"},
        follow_redirects=False,
    )
    assert integrated.status_code == 303
    with main.connect_db() as db:
        article = db.execute(
            "SELECT content, version FROM articles WHERE id = 1"
        ).fetchone()
        proposal = db.execute(
            "SELECT status, reviewed_by FROM article_proposals WHERE id = 1"
        ).fetchone()
    accepted_blocks = json.loads(article["content"])["blocks"]
    assert {"type": "heading", "text": "Etapa adicional"} in accepted_blocks
    assert {"type": "paragraph", "text": "Verifique o painel antes da operação."} in accepted_blocks
    assert article["version"] == 2
    assert proposal["status"] == "accepted"
    assert proposal["reviewed_by"] == 3


def test_admin_only_app_customization_changes_brand_and_navigation(app_client):
    assert main.app.title == "Central_Wiki"
    assert main.app_configuration()["app_name"] == "Central_Wiki"

    editor_csrf = sign_in(app_client)
    denied = app_client.get("/admin/settings")
    assert denied.status_code == 403
    denied_post = app_client.post(
        "/admin/settings",
        data={"csrf_token": editor_csrf},
    )
    assert denied_post.status_code == 403

    admin_csrf = sign_in(app_client, "admin", "admin-password-123")
    logo = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + b"\x00\x00\x00\x01\x00\x00\x00\x01"
    settings = {
        "csrf_token": admin_csrf,
        "app_name": "Wiki Interna",
        "page_home": "Início",
        "page_articles": "Manuais",
        "page_admin": "Equipe e áreas",
        "page_audit": "Registros",
        "page_settings": "Aparência",
        "page_account": "Conta pessoal",
        "button_new_article": "Adicionar manual",
        "button_save_draft": "Guardar",
        "button_submit_review": "Solicitar revisão",
        "button_edit_article": "Alterar manual",
        "button_publish": "Liberar conteúdo",
    }
    response = app_client.post(
        "/admin/settings",
        data=settings,
        files={"logo": ("logo.png", logo, "image/png")},
        follow_redirects=False,
    )
    assert response.status_code == 303
    page = app_client.get("/")
    assert "Wiki Interna" in page.text
    assert "<title>Wiki Interna</title>" in page.text
    assert "Manuais · Wiki Interna" not in page.text
    assert "Manuais" in page.text
    assert "Adicionar manual" in page.text
    assert "Equipe e áreas" in page.text
    assert "Conta pessoal" in page.text
    assert 'rel="icon" href="/favicon.ico?v=2" type="image/png"' in page.text
    assert "/branding/logo?v=2" in page.text
    logo_response = app_client.get("/branding/logo")
    assert logo_response.content == logo
    assert logo_response.headers["content-type"] == "image/png"
    favicon_response = app_client.get("/favicon.ico?v=2")
    assert favicon_response.content == logo
    assert favicon_response.headers["content-type"] == "image/png"

    article_page = app_client.get("/articles")
    assert "<title>Wiki Interna</title>" in article_page.text
    assert "Manuais · Wiki Interna" not in article_page.text


def test_profile_photo_is_private_to_authenticated_account(app_client):
    photo = app_client.get("/account/profile/photo", follow_redirects=False)
    assert photo.status_code == 303
    assert photo.headers["location"] == "/login"
    sign_in(app_client, "admin", "admin-password-123")
    assert app_client.get("/account/profile/photo").status_code == 404


def test_initialize_database_migrates_existing_user_names(tmp_path, monkeypatch):
    database_path = tmp_path / "legacy.db"
    monkeypatch.setattr(main, "DB_PATH", database_path)
    with sqlite3.connect(database_path) as db:
        db.execute(
            """
            CREATE TABLE users (
                id INTEGER PRIMARY KEY,
                username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                is_admin INTEGER NOT NULL DEFAULT 0,
                active INTEGER NOT NULL DEFAULT 1,
                session_version INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
    main.initialize_database()
    with main.connect_db() as db:
        columns = {row["name"] for row in db.execute("PRAGMA table_info(users)")}
        assert {"full_name", "profile_photo", "profile_photo_type"} <= columns
        assert db.execute("SELECT full_name FROM users").fetchall() == []


@pytest.mark.parametrize(
    ("stored_name", "expected_name"),
    [("Consat_Wiki", "Central_Wiki"), ("Wiki Interna", "Wiki Interna")],
)
def test_initialize_database_migrates_brand_configuration(
    tmp_path, monkeypatch, stored_name, expected_name
):
    database_path = tmp_path / "legacy-brand.db"
    monkeypatch.setattr(main, "DB_PATH", database_path)
    with sqlite3.connect(database_path) as db:
        db.execute(
            """
            CREATE TABLE app_configuration (
                id INTEGER PRIMARY KEY CHECK(id = 1),
                app_name TEXT NOT NULL DEFAULT 'Consat_Wiki',
                page_home TEXT NOT NULL DEFAULT 'Visão geral',
                page_articles TEXT NOT NULL DEFAULT 'Base de conhecimento',
                page_admin TEXT NOT NULL DEFAULT 'Usuários e setores',
                page_audit TEXT NOT NULL DEFAULT 'Auditoria',
                button_new_article TEXT NOT NULL DEFAULT 'Novo artigo',
                button_save_draft TEXT NOT NULL DEFAULT 'Salvar rascunho',
                button_submit_review TEXT NOT NULL DEFAULT 'Enviar para revisão',
                button_edit_article TEXT NOT NULL DEFAULT 'Editar artigo',
                button_publish TEXT NOT NULL DEFAULT 'Aprovar',
                logo BLOB,
                logo_type TEXT
            )
            """
        )
        db.execute(
            "INSERT INTO app_configuration (id, app_name) VALUES (1, ?)",
            (stored_name,),
        )
    main.initialize_database()
    config = main.app_configuration()
    assert config["app_name"] == expected_name
    assert config["page_settings"] == "Personalizar aplicativo"
    assert config["page_account"] == "Minha conta"
    assert config["brand_version"] == 1


def test_database_integrity_check_reports_ok_and_corruption(tmp_path, monkeypatch):
    database_path = tmp_path / "wiki.db"
    monkeypatch.setattr(main, "DB_PATH", database_path)
    main.initialize_database()
    main.check_database_integrity()

    database_path.write_bytes(b"not a SQLite database")
    with pytest.raises(SystemExit, match="Não foi possível verificar"):
        main.check_database_integrity()


def test_sector_isolation_applies_to_article_pages_and_api(app_client):
    sign_in(app_client)
    response = app_client.get("/articles/2")
    assert response.status_code == 403
    articles = app_client.get("/api/v1/articles").json()
    assert [article["title"] for article in articles] == ["Procedimento"]
    assert "Treinamento" in [sector["name"] for sector in app_client.get("/api/v1/sectors").json()]


def test_article_detail_hides_revision_history_and_uses_responsive_layout(app_client):
    sign_in(app_client)

    response = app_client.get("/articles/1")

    assert response.status_code == 200
    assert "Histórico de alterações" not in response.text
    assert 'class="reading-page"' in response.text


def test_article_list_paginates_and_preserves_sector_filter(app_client):
    sign_in(app_client, "admin", "admin-password-123")
    with main.connect_db() as db:
        db.executemany(
            """
            INSERT INTO articles (title, content, sector_id, status, author_id)
            VALUES (?, 'Conteúdo de teste', 1, 'published', 2)
            """,
            [(f"Artigo extra {index}",) for index in range(55)],
        )

    first_page = app_client.get("/articles?sector_id=1")
    second_page = app_client.get("/articles?sector_id=1&page=2")

    assert first_page.status_code == 200
    assert first_page.text.count('class="article-row"') == 50
    assert "56 artigos" in first_page.text
    assert second_page.status_code == 200
    assert second_page.text.count('class="article-row"') == 6
    assert "página 2 de 2" in second_page.text
    assert 'option value="1" selected' in second_page.text


def test_article_api_supports_bounded_pagination(app_client):
    sign_in(app_client, "admin", "admin-password-123")

    response = app_client.get("/api/v1/articles?limit=1&offset=1")

    assert response.status_code == 200
    assert len(response.json()) == 1
    assert app_client.get("/api/v1/articles?limit=101").status_code == 422


def test_admin_reader_preview_is_scoped_read_only_and_restorable(app_client):
    csrf = sign_in(app_client, "admin", "admin-password-123")
    with main.connect_db() as db:
        db.execute(
            """
            INSERT INTO articles (title, content, sector_id, status, author_id)
            VALUES ('Rascunho invisível', 'Conteúdo privado', 1, 'review', 2)
            """
        )

    response = app_client.post(
        "/admin/preview",
        data={"csrf_token": csrf, "action": "start", "sector_id": 1},
        follow_redirects=False,
    )

    assert response.status_code == 303
    dashboard = app_client.get("/")
    assert dashboard.status_code == 200
    assert "Modo de visualização" in dashboard.text
    assert "Administrador" not in dashboard.text
    assert "Usuários e setores" not in dashboard.text
    assert app_client.get("/articles").status_code == 200
    article_list = app_client.get("/articles")
    assert "Procedimento" in article_list.text
    assert "Material restrito" not in article_list.text
    assert "Rascunho invisível" not in article_list.text
    assert app_client.get("/api/v1/sectors").json() == [{"id": 1, "name": "Monitoramento"}]
    api_articles = app_client.get("/api/v1/articles").json()
    assert [article["title"] for article in api_articles] == ["Procedimento"]
    assert app_client.get("/articles/2").status_code == 404
    assert app_client.get("/admin").status_code == 403
    assert app_client.get("/articles/new").status_code == 403

    preview_csrf = main.decode_session(app_client.cookies.get(main.COOKIE_NAME))["csrf"]
    with main.connect_db() as db:
        before = db.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
    blocked_write = app_client.post(
        "/articles/new",
        data={
            "csrf_token": preview_csrf,
            "title": "Não deve ser criado",
            "content": "Teste",
            "sector_id": 1,
            "action": "draft",
        },
        follow_redirects=False,
    )
    assert blocked_write.status_code == 403
    with main.connect_db() as db:
        assert db.execute("SELECT COUNT(*) FROM articles").fetchone()[0] == before

    stop_response = app_client.post(
        "/admin/preview",
        data={"csrf_token": preview_csrf, "action": "stop"},
        follow_redirects=False,
    )
    assert stop_response.status_code == 303
    restored_admin_page = app_client.get("/admin")
    assert restored_admin_page.status_code == 200
    assert "Visualizar como usuário comum" in restored_admin_page.text
    assert app_client.get("/articles/2").status_code == 200


def test_non_admin_cannot_start_reader_preview(app_client):
    csrf = sign_in(app_client)
    response = app_client.post(
        "/admin/preview",
        data={"csrf_token": csrf, "action": "start", "sector_id": 1},
        follow_redirects=False,
    )
    assert response.status_code == 403


def test_editor_can_submit_for_review_and_content_is_escaped(app_client):
    csrf = sign_in(app_client)
    response = app_client.post(
        "/articles/new",
        data={
            "csrf_token": csrf,
            "title": "Alerta",
            "content": "<script>alert(1)</script>",
            "sector_id": 1,
            "action": "review",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    article_response = app_client.get(response.headers["location"])
    assert article_response.status_code == 200
    assert "&lt;script&gt;" in article_response.text
    assert "<script>alert(1)</script>" not in article_response.text
    with main.connect_db() as db:
        row = db.execute("SELECT status FROM articles WHERE title = 'Alerta'").fetchone()
    assert row["status"] == "review"


def test_article_editor_offers_safe_content_modules(app_client):
    sign_in(app_client)
    response = app_client.get("/articles/new")
    assert response.status_code == 200
    assert 'data-add-module="youtube"' in response.text
    assert 'data-add-module="image"' in response.text
    assert f'data-max-videos="{main.MAX_ARTICLE_VIDEOS}"' in response.text
    assert f'data-max-blocks="{main.MAX_ARTICLE_BLOCKS}"' in response.text
    assert "HTML e JavaScript não são executados." in response.text


def test_article_modules_render_youtube_and_keep_untrusted_markup_escaped(app_client):
    csrf = sign_in(app_client)
    content = {
        "format": main.ARTICLE_BLOCK_FORMAT,
        "blocks": [
            {"type": "heading", "text": "Passo principal"},
            {"type": "paragraph", "text": "<script>alert('xss')</script>"},
            {"type": "image", "url": "https://images.example.com/manual.png", "alt": "Manual"},
            {"type": "youtube", "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
        ],
    }
    response = app_client.post(
        "/articles/new",
        data={
            "csrf_token": csrf,
            "title": "Artigo com módulos",
            "content": json.dumps(content),
            "sector_id": 1,
            "action": "review",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303

    detail = app_client.get(response.headers["location"])
    assert detail.status_code == 200
    assert '<h2 class="article-block-heading">Passo principal</h2>' in detail.text
    assert "&lt;script&gt;alert(&#39;xss&#39;)&lt;/script&gt;" in detail.text
    assert "<script>alert('xss')</script>" not in detail.text
    assert 'src="https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ"' in detail.text
    assert 'sandbox="allow-scripts allow-same-origin allow-presentation"' in detail.text
    assert 'src="https://images.example.com/manual.png"' in detail.text

    with main.connect_db() as db:
        saved = db.execute(
            "SELECT content FROM articles WHERE title = 'Artigo com módulos'"
        ).fetchone()["content"]
    document = json.loads(saved)
    assert document["format"] == main.ARTICLE_BLOCK_FORMAT
    assert document["blocks"][-1] == {"type": "youtube", "video_id": "dQw4w9WgXcQ"}
    edit_page = app_client.get(f'{response.headers["location"]}/edit')
    assert edit_page.status_code == 200
    assert "https://www.youtube.com/watch?v=dQw4w9WgXcQ" in edit_page.text


def test_article_module_urls_and_youtube_video_count_are_validated(app_client):
    csrf = sign_in(app_client)
    invalid_content = {
        "format": main.ARTICLE_BLOCK_FORMAT,
        "blocks": [
            {"type": "image", "url": "javascript:alert(1)", "alt": "Imagem maliciosa"},
        ],
    }
    response = app_client.post(
        "/articles/new",
        data={
            "csrf_token": csrf,
            "title": "URL inválida",
            "content": json.dumps(invalid_content),
            "sector_id": 1,
        },
    )
    assert response.status_code == 400
    assert "HTTPS público" in response.text
    with main.connect_db() as db:
        assert db.execute(
            "SELECT 1 FROM articles WHERE title = 'URL inválida'"
        ).fetchone() is None

    too_many_videos = {
        "format": main.ARTICLE_BLOCK_FORMAT,
        "blocks": [
            {"type": "youtube", "url": f"https://youtu.be/{index:011d}"}
            for index in range(main.MAX_ARTICLE_VIDEOS + 1)
        ],
    }
    response = app_client.post(
        "/articles/new",
        data={
            "csrf_token": csrf,
            "title": "Muitos vídeos",
            "content": json.dumps(too_many_videos),
            "sector_id": 1,
        },
    )
    assert response.status_code == 400
    assert f"até {main.MAX_ARTICLE_VIDEOS} vídeos" in response.text

    exactly_ten_videos = {
        "format": main.ARTICLE_BLOCK_FORMAT,
        "blocks": [
            {"type": "youtube", "url": f"https://youtu.be/{index:011d}"}
            for index in range(main.MAX_ARTICLE_VIDEOS)
        ],
    }
    response = app_client.post(
        "/articles/new",
        data={
            "csrf_token": csrf,
            "title": "Dez vídeos",
            "content": json.dumps(exactly_ten_videos),
            "sector_id": 1,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert app_client.get(response.headers["location"]).text.count(
        "<iframe "
    ) == main.MAX_ARTICLE_VIDEOS

    unsupported_host = {
        "format": main.ARTICLE_BLOCK_FORMAT,
        "blocks": [
            {"type": "youtube", "url": "https://youtube.com.attacker.example/watch?v=dQw4w9WgXcQ"},
        ],
    }
    response = app_client.post(
        "/articles/new",
        data={
            "csrf_token": csrf,
            "title": "Domínio falso",
            "content": json.dumps(unsupported_host),
            "sector_id": 1,
        },
    )
    assert response.status_code == 400


def test_youtube_link_variants_and_image_local_hosts():
    assert main.youtube_video_id("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert main.youtube_video_id(
        "https://www.youtube.com/shorts/dQw4w9WgXcQ"
    ) == "dQw4w9WgXcQ"
    assert main.youtube_video_id(
        "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ"
    ) == "dQw4w9WgXcQ"
    with pytest.raises(ValueError, match="HTTPS público"):
        main.validate_image_url("https://localhost./image.png")
    with pytest.raises(ValueError, match="endereço da imagem precisa ser público"):
        main.validate_image_url("https://127.0.0.1/image.png")


def test_csrf_is_required_for_mutations(app_client):
    sign_in(app_client)
    response = app_client.post(
        "/articles/new",
        data={"title": "Sem token", "content": "Conteúdo", "sector_id": 1, "action": "draft"},
    )
    assert response.status_code == 403
    with main.connect_db() as db:
        assert db.execute("SELECT 1 FROM articles WHERE title = 'Sem token'").fetchone() is None


def test_editor_cannot_publish_but_admin_can(app_client):
    sign_in(app_client)
    with main.connect_db() as db:
        db.execute(
            """
            INSERT INTO articles (title, content, sector_id, status, author_id)
            VALUES ('Pendente', 'Conteúdo', 1, 'review', 2)
            """
        )
        article_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    csrf = main.decode_session(app_client.cookies.get(main.COOKIE_NAME))["csrf"]
    response = app_client.post(
        f"/articles/{article_id}/publish",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 403

    sign_in(app_client, "admin", "admin-password-123")
    csrf = main.decode_session(app_client.cookies.get(main.COOKIE_NAME))["csrf"]
    response = app_client.post(
        f"/articles/{article_id}/publish",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 303
    with main.connect_db() as db:
        row = db.execute("SELECT status FROM articles WHERE id = ?", (article_id,)).fetchone()
    assert row["status"] == "published"


def test_editor_cannot_move_article_into_sector_without_edit_permission(app_client):
    csrf = sign_in(app_client)
    with main.connect_db() as db:
        db.execute(
            """
            INSERT INTO articles (title, content, sector_id, status, author_id)
            VALUES ('Meu artigo', 'Conteúdo', 1, 'draft', 2)
            """
        )
        article_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    response = app_client.post(
        f"/articles/{article_id}/edit",
        data={
            "csrf_token": csrf,
            "title": "Meu artigo",
            "content": "Conteúdo alterado",
            "sector_id": 2,
            "version": 1,
            "action": "draft",
        },
    )
    assert response.status_code == 403
    with main.connect_db() as db:
        row = db.execute("SELECT sector_id FROM articles WHERE id = ?", (article_id,)).fetchone()
    assert row["sector_id"] == 1


def test_editing_published_article_sends_changes_back_to_review(app_client):
    csrf = sign_in(app_client)
    response = app_client.post(
        "/articles/1/edit",
        data={
            "csrf_token": csrf,
            "title": "Procedimento atualizado",
            "content": "Nova versão",
            "sector_id": 1,
            "version": 1,
            "action": "draft",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    with main.connect_db() as db:
        article = db.execute("SELECT status, version FROM articles WHERE id = 1").fetchone()
        revision = db.execute(
            "SELECT status FROM article_revisions WHERE article_id = 1 ORDER BY version DESC LIMIT 1"
        ).fetchone()
    assert article["status"] == "review"
    assert article["version"] == 2
    assert revision["status"] == "review"


def test_stale_article_form_cannot_overwrite_newer_changes(app_client):
    csrf = sign_in(app_client)
    first = {
        "csrf_token": csrf,
        "title": "Procedimento v2",
        "content": "Primeira alteração",
        "sector_id": 1,
        "version": 1,
        "action": "draft",
    }
    saved = app_client.post(
        "/articles/1/edit",
        data=first,
        follow_redirects=False,
    )
    assert saved.status_code == 303

    stale = {
        **first,
        "title": "Procedimento sobrescrito",
        "content": "Alteração obsoleta",
    }
    conflict = app_client.post("/articles/1/edit", data=stale)
    assert conflict.status_code == 409
    assert "alterado desde que você o abriu" in conflict.json()["detail"]
    with main.connect_db() as db:
        article = db.execute("SELECT title, version FROM articles WHERE id = 1").fetchone()
    assert article["title"] == "Procedimento v2"
    assert article["version"] == 2


def test_password_change_invalidates_old_session(app_client):
    csrf = sign_in(app_client)
    old_cookie = app_client.cookies.get(main.COOKIE_NAME)
    response = app_client.post(
        "/account/password",
        data={
            "csrf_token": csrf,
            "current_password": "editor-password-123",
            "new_password": "new-editor-password-456",
            "confirmation": "new-editor-password-456",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    new_cookie = app_client.cookies.get(main.COOKIE_NAME)
    assert new_cookie != old_cookie
    with TestClient(main.app) as old_session_client:
        old_session_client.cookies.set(main.COOKIE_NAME, old_cookie)
        response = old_session_client.get("/", follow_redirects=False)
        assert response.status_code == 303


def test_admin_can_create_user_and_assign_sector_role(app_client):
    csrf = sign_in(app_client, "admin", "admin-password-123")
    assert app_client.get("/admin").status_code == 200
    response = app_client.post(
        "/admin/users",
        data={
            "csrf_token": csrf,
            "username": "nova-pessoa",
            "full_name": "Nova Pessoa",
            "password": "senha-inicial-123",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    with main.connect_db() as db:
        created_user = db.execute(
            "SELECT id, full_name FROM users WHERE username = 'nova-pessoa'"
        ).fetchone()
        user_id = created_user["id"]
        assert created_user["full_name"] == "Nova Pessoa"
    response = app_client.post(
        "/admin/memberships",
        data={
            "csrf_token": csrf,
            "user_id": user_id,
            "sector_id": 1,
            "role": "approver",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert main.role_for(user_id, 1) == "approver"


def test_admin_creation_validates_lengths_and_checkbox_values(app_client):
    csrf = sign_in(app_client, "admin", "admin-password-123")

    response = app_client.post(
        "/admin/users",
        data={
            "csrf_token": csrf,
            "username": "u" * main.MAX_USERNAME_LENGTH,
            "full_name": "Nome Sobrenome",
            "password": "senha-inicial-123",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    with main.connect_db() as db:
        assert db.execute(
            "SELECT 1 FROM users WHERE username = ?",
            ("u" * main.MAX_USERNAME_LENGTH,),
        ).fetchone() is not None

    invalid_users = (
        ("ab", "senha-inicial-123"),
        ("u" * (main.MAX_USERNAME_LENGTH + 1), "senha-inicial-123"),
        ("senha-curta", "curta"),
    )
    for username, password in invalid_users:
        response = app_client.post(
            "/admin/users",
            data={
                "csrf_token": csrf,
                "username": username,
                "full_name": "Nome Sobrenome",
                "password": password,
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert "error=user-validation" in response.headers["location"]

    response = app_client.post(
        "/admin/users",
        data={
            "csrf_token": csrf,
            "username": "conta-invalida",
            "full_name": "Conta Inválida",
            "password": "senha-inicial-123",
            "is_admin": "true",
        },
        follow_redirects=False,
    )
    assert response.status_code == 400
    with main.connect_db() as db:
        assert db.execute(
            "SELECT 1 FROM users WHERE username = 'conta-invalida'"
        ).fetchone() is None


def test_admin_creation_normalizes_and_reports_duplicate_names(app_client):
    csrf = sign_in(app_client, "admin", "admin-password-123")

    response = app_client.post(
        "/admin/sectors",
        data={"csrf_token": csrf, "name": "   "},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "error=sector-validation" in response.headers["location"]

    response = app_client.post(
        "/admin/sectors",
        data={"csrf_token": csrf, "name": "s" * (main.MAX_SECTOR_NAME_LENGTH + 1)},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "error=sector-validation" in response.headers["location"]

    response = app_client.post(
        "/admin/sectors",
        data={"csrf_token": csrf, "name": "s" * main.MAX_SECTOR_NAME_LENGTH},
        follow_redirects=False,
    )
    assert response.status_code == 303

    response = app_client.post(
        "/admin/sectors",
        data={"csrf_token": csrf, "name": "  monitoramento  "},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "error=duplicate-sector" in response.headers["location"]

    response = app_client.post(
        "/admin/users",
        data={
            "csrf_token": csrf,
            "username": " ADMIN ",
            "full_name": "Administrador Duplicado",
            "password": "senha-inicial-123",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "error=duplicate-user" in response.headers["location"]


def test_admin_memberships_reject_missing_and_admin_targets(app_client):
    csrf = sign_in(app_client, "admin", "admin-password-123")

    for user_id, sector_id, expected_status in (
        (999, 1, 404),
        (2, 999, 404),
        (1, 1, 400),
    ):
        response = app_client.post(
            "/admin/memberships",
            data={
                "csrf_token": csrf,
                "user_id": user_id,
                "sector_id": sector_id,
                "role": "reader",
            },
            follow_redirects=False,
        )
        assert response.status_code == expected_status

    assert main.role_for(1, 1) is None
