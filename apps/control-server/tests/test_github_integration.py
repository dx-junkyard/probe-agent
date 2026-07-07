"""Unit tests for github_integration.py (Issue #158 External Issue Loop).

Covers owner/repo detection from the origin remote / explicit override, and
that availability fails closed when the token or owner/repo can't be
determined -- never falls back to a guess (Principle 6).
"""

import subprocess

import pytest

from app.github_integration import (
    GitHubIntegrationError,
    detect_github_repo,
    resolve_github_config,
)


def _init_repo(tmp_path, remote_url=None):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=str(repo), check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=str(repo), check=True, capture_output=True,
    )
    (repo / "README.md").write_text("# test\n")
    subprocess.run(["git", "add", "."], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"], cwd=str(repo), check=True, capture_output=True
    )
    if remote_url:
        subprocess.run(
            ["git", "remote", "add", "origin", remote_url],
            cwd=str(repo), check=True, capture_output=True,
        )
    return str(repo)


class TestDetectGitHubRepo:
    def test_https_remote(self, tmp_path):
        repo_path = _init_repo(tmp_path, "https://github.com/acme/widgets.git")
        assert detect_github_repo(repo_path) == ("acme", "widgets")

    def test_ssh_remote(self, tmp_path):
        repo_path = _init_repo(tmp_path, "git@github.com:acme/widgets.git")
        assert detect_github_repo(repo_path) == ("acme", "widgets")

    def test_non_github_remote_is_none(self, tmp_path):
        repo_path = _init_repo(tmp_path, "https://gitlab.com/acme/widgets.git")
        assert detect_github_repo(repo_path) is None

    def test_no_remote_is_none(self, tmp_path):
        repo_path = _init_repo(tmp_path)
        assert detect_github_repo(repo_path) is None

    def test_explicit_env_override_wins(self, tmp_path, monkeypatch):
        repo_path = _init_repo(tmp_path, "https://gitlab.com/acme/widgets.git")
        monkeypatch.setenv("GITHUB_REPO", "other/name")
        assert detect_github_repo(repo_path) == ("other", "name")


class TestResolveGitHubConfig:
    def test_none_without_token(self, tmp_path, monkeypatch):
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        repo_path = _init_repo(tmp_path, "https://github.com/acme/widgets.git")
        assert resolve_github_config(repo_path) is None

    def test_none_without_resolvable_repo(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "t")
        monkeypatch.delenv("GITHUB_REPO", raising=False)
        repo_path = _init_repo(tmp_path)
        assert resolve_github_config(repo_path) is None

    def test_config_when_both_present(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "t")
        repo_path = _init_repo(tmp_path, "git@github.com:acme/widgets.git")
        config = resolve_github_config(repo_path)
        assert config is not None
        assert config.token == "t"
        assert config.owner == "acme"
        assert config.repo == "widgets"


class TestCreateGitHubIssueErrors:
    def test_http_error_raises_integration_error(self, monkeypatch):
        import urllib.error

        from app.github_integration import GitHubRepoConfig, create_github_issue

        class FakeHTTPError(urllib.error.HTTPError):
            def __init__(self):
                super().__init__("url", 403, "Forbidden", {}, None)

            def read(self):
                return b'{"message": "rate limited"}'

        def fake_urlopen(request, timeout=30):
            raise FakeHTTPError()

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        config = GitHubRepoConfig(token="t", owner="acme", repo="widgets")
        with pytest.raises(GitHubIntegrationError, match="403"):
            create_github_issue(config, "title", "body")
