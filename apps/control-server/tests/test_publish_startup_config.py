"""Issue #224: GITHUB_PUBLISH_ENABLED startup validation.

Covers `app.github_app.validate_publish_startup_config()` directly (each
RuntimeError path in isolation) and once through `app.db.init_db()` with a
monkeypatched `PROBE_DB_PATH` to prove the wiring (called right after
`_validate_startup_environment()`, following Issue #225's pattern). Also
covers `publish_enabled()`'s finite-set parsing directly.

Every RuntimeError assertion also checks that neither the PEM key content
nor the configured path value ever appears in the message -- only the env
var name may appear (CLAUDE.md: never log/persist/echo secrets or host
paths).
"""

import pytest


@pytest.fixture
def rsa_private_key_path(tmp_path):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    path = tmp_path / "github-app-key.pem"
    path.write_bytes(pem)
    return str(path)


def _clean_env(monkeypatch):
    monkeypatch.delenv("GITHUB_PUBLISH_ENABLED", raising=False)
    monkeypatch.delenv("GITHUB_APP_ID", raising=False)
    monkeypatch.delenv("GITHUB_APP_PRIVATE_KEY_PATH", raising=False)


def _assert_no_secret_leak(message, *, pem_bytes=None, path_value=None):
    if pem_bytes:
        text = pem_bytes.decode("ascii", errors="ignore")
        # The PEM body itself (base64 lines) must not appear verbatim.
        for line in text.splitlines():
            line = line.strip()
            if line and "BEGIN" not in line and "END" not in line:
                assert line not in message
    if path_value:
        assert path_value not in message


# --- publish_enabled() finite-set parsing -----------------------------------


class TestPublishEnabledParsing:
    @pytest.mark.parametrize("raw", ["", "false", "0", "no", "off", "FALSE", "Off"])
    def test_falsey_values(self, monkeypatch, raw):
        from app.github_app import publish_enabled

        if raw == "":
            monkeypatch.delenv("GITHUB_PUBLISH_ENABLED", raising=False)
        else:
            monkeypatch.setenv("GITHUB_PUBLISH_ENABLED", raw)
        assert publish_enabled() is False

    @pytest.mark.parametrize("raw", ["true", "1", "yes", "on", "TRUE", "On"])
    def test_truthy_values(self, monkeypatch, raw):
        from app.github_app import publish_enabled

        monkeypatch.setenv("GITHUB_PUBLISH_ENABLED", raw)
        assert publish_enabled() is True

    def test_unrecognized_value_raises(self, monkeypatch):
        from app.github_app import publish_enabled

        monkeypatch.setenv("GITHUB_PUBLISH_ENABLED", "banana")
        with pytest.raises(RuntimeError, match="GITHUB_PUBLISH_ENABLED"):
            publish_enabled()


# --- validate_publish_startup_config() direct calls -------------------------


class TestValidatePublishStartupConfigDirect:
    def test_disabled_and_nothing_configured_passes(self, monkeypatch):
        _clean_env(monkeypatch)
        from app.github_app import validate_publish_startup_config

        validate_publish_startup_config()  # must not raise

    def test_unset_treated_as_disabled(self, monkeypatch):
        _clean_env(monkeypatch)
        monkeypatch.delenv("GITHUB_PUBLISH_ENABLED", raising=False)
        from app.github_app import validate_publish_startup_config

        validate_publish_startup_config()  # must not raise

    def test_enabled_without_app_id_fails(self, monkeypatch, rsa_private_key_path):
        _clean_env(monkeypatch)
        monkeypatch.setenv("GITHUB_PUBLISH_ENABLED", "true")
        monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY_PATH", rsa_private_key_path)
        from app.github_app import validate_publish_startup_config

        with pytest.raises(RuntimeError, match="GITHUB_APP_ID") as exc_info:
            validate_publish_startup_config()
        _assert_no_secret_leak(str(exc_info.value), path_value=rsa_private_key_path)

    def test_enabled_with_missing_file_fails(self, monkeypatch, tmp_path):
        _clean_env(monkeypatch)
        monkeypatch.setenv("GITHUB_PUBLISH_ENABLED", "true")
        monkeypatch.setenv("GITHUB_APP_ID", "123")
        missing_path = str(tmp_path / "missing.pem")
        monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY_PATH", missing_path)
        from app.github_app import validate_publish_startup_config

        with pytest.raises(
            RuntimeError, match="GITHUB_APP_PRIVATE_KEY_PATH"
        ) as exc_info:
            validate_publish_startup_config()
        _assert_no_secret_leak(str(exc_info.value), path_value=missing_path)

    def test_enabled_with_empty_file_fails(self, monkeypatch, tmp_path):
        _clean_env(monkeypatch)
        monkeypatch.setenv("GITHUB_PUBLISH_ENABLED", "true")
        monkeypatch.setenv("GITHUB_APP_ID", "123")
        empty_path = tmp_path / "empty.pem"
        empty_path.write_bytes(b"")
        monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY_PATH", str(empty_path))
        from app.github_app import validate_publish_startup_config

        with pytest.raises(
            RuntimeError, match="GITHUB_APP_PRIVATE_KEY_PATH"
        ) as exc_info:
            validate_publish_startup_config()
        _assert_no_secret_leak(str(exc_info.value), path_value=str(empty_path))

    def test_enabled_with_garbage_file_fails(self, monkeypatch, tmp_path):
        _clean_env(monkeypatch)
        monkeypatch.setenv("GITHUB_PUBLISH_ENABLED", "true")
        monkeypatch.setenv("GITHUB_APP_ID", "123")
        garbage_path = tmp_path / "garbage.pem"
        garbage_path.write_bytes(b"this is not a pem private key at all\n")
        monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY_PATH", str(garbage_path))
        from app.github_app import validate_publish_startup_config

        with pytest.raises(
            RuntimeError, match="GITHUB_APP_PRIVATE_KEY_PATH"
        ) as exc_info:
            validate_publish_startup_config()
        _assert_no_secret_leak(str(exc_info.value), path_value=str(garbage_path))

    def test_enabled_with_valid_pem_and_app_id_passes(
        self, monkeypatch, rsa_private_key_path
    ):
        _clean_env(monkeypatch)
        monkeypatch.setenv("GITHUB_PUBLISH_ENABLED", "true")
        monkeypatch.setenv("GITHUB_APP_ID", "123")
        monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY_PATH", rsa_private_key_path)
        from app.github_app import validate_publish_startup_config

        validate_publish_startup_config()  # must not raise

    def test_unrecognized_publish_enabled_value_fails(self, monkeypatch):
        _clean_env(monkeypatch)
        monkeypatch.setenv("GITHUB_PUBLISH_ENABLED", "banana")
        from app.github_app import validate_publish_startup_config

        with pytest.raises(RuntimeError, match="GITHUB_PUBLISH_ENABLED"):
            validate_publish_startup_config()


# --- Wiring through app.db.init_db() ----------------------------------------


def _clean_auth_env(monkeypatch):
    # Keep other startup checks (Issue #225) out of the way so only the
    # publish-config validation path is exercised.
    monkeypatch.delenv("CONTROL_ENV", raising=False)
    monkeypatch.delenv("CONTROL_REQUIRE_AUTH", raising=False)
    monkeypatch.delenv("CONTROL_API_KEYS", raising=False)
    monkeypatch.delenv("CONTROL_ADMIN_USERNAME", raising=False)
    monkeypatch.delenv("CONTROL_ADMIN_PASSWORD", raising=False)


class TestInitDbWiring:
    def test_init_db_fails_when_publish_enabled_without_app_id(
        self, tmp_path, monkeypatch, rsa_private_key_path
    ):
        _clean_env(monkeypatch)
        _clean_auth_env(monkeypatch)
        monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "publish-startup-1.db"))
        monkeypatch.setenv("GITHUB_PUBLISH_ENABLED", "true")
        monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY_PATH", rsa_private_key_path)
        from app import db  # noqa: WPS433

        with pytest.raises(RuntimeError, match="GITHUB_APP_ID"):
            db.init_db()

    def test_init_db_passes_when_publish_disabled(self, tmp_path, monkeypatch):
        _clean_env(monkeypatch)
        _clean_auth_env(monkeypatch)
        monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "publish-startup-2.db"))
        from app import db  # noqa: WPS433

        db.init_db()  # must not raise

    def test_init_db_passes_when_publish_enabled_and_valid(
        self, tmp_path, monkeypatch, rsa_private_key_path
    ):
        _clean_env(monkeypatch)
        _clean_auth_env(monkeypatch)
        monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "publish-startup-3.db"))
        monkeypatch.setenv("GITHUB_PUBLISH_ENABLED", "true")
        monkeypatch.setenv("GITHUB_APP_ID", "123")
        monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY_PATH", rsa_private_key_path)
        from app import db  # noqa: WPS433

        db.init_db()  # must not raise
