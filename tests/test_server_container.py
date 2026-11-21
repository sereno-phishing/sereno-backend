"""Structural checks of the evaluation server container: the Dockerfile, the
.dockerignore and the `server` service of docker-compose.yml. The image build
itself is checked by hand (see README, "Run the three containers")."""
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def read(name):
    return (ROOT / name).read_text(encoding="utf-8")


def service(name):
    """The text of one compose service: its lines, up to the next key at the
    same or a lower indentation."""
    match = re.search(rf"^  {name}:\n((?:(?:    .*)?\n)+)", read("docker-compose.yml"),
                      flags=re.MULTILINE)
    assert match, f"docker-compose.yml has no '{name}' service"
    return match.group(1)


def instructions(keyword):
    """The arguments of every Dockerfile instruction of that keyword."""
    return re.findall(rf"^{keyword}\s+(.+)$", read("Dockerfile"), flags=re.MULTILINE)


def test_the_server_starts_only_with_its_profile():
    assert re.search(r'^    profiles: \["server"\]$', service("server"), flags=re.MULTILINE)
    for name in ("postgres", "redis"):
        assert "profiles:" not in service(name)


def test_the_server_is_published_on_the_loopback_address_the_extension_uses():
    ports = re.findall(r'^      - "(\S+)"$', service("server"), flags=re.MULTILINE)
    assert ports == ["127.0.0.1:5000:5000"]


def test_the_server_waits_for_healthy_storage_and_reports_its_own_health():
    text = service("server")
    for name in ("postgres", "redis"):
        assert re.search(rf"^      {name}:\n        condition: service_healthy$", text,
                         flags=re.MULTILINE)
    assert re.search(r"^    healthcheck:\n      test: .*/v1/model", text, flags=re.MULTILINE)


def test_the_server_is_configured_through_the_settings_of_the_application():
    text = service("server")
    assert re.search(r"^      CLASSIFIER: model$", text, flags=re.MULTILINE)
    # The other services by their compose names, with the compose credentials.
    assert re.search(r"^      DATABASE_URL: postgresql://\$\{POSTGRES_USER:-sereno\}:"
                     r"\$\{POSTGRES_PASSWORD:-sereno\}@postgres:5432/\$\{POSTGRES_DB:-sereno\}$",
                     text, flags=re.MULTILINE)
    assert re.search(r"^      REDIS_URL: redis://redis:6379/0$", text, flags=re.MULTILINE)
    # Neither works nor belongs behind a published port (see the README).
    assert not re.search(r"^\s+(AUTH_LOCAL_HTTP|DEV_TOOLS):", text, flags=re.MULTILINE)
    # On unless the host asks for the run without cache (response-time experiment).
    assert re.search(r"^      CACHE_ENABLED: \$\{CACHE_ENABLED:-1\}$", text, flags=re.MULTILINE)


def test_the_server_image_is_built_from_this_folder_and_the_model_repository():
    text = service("server")
    assert re.search(r"^      context: \.$", text, flags=re.MULTILINE)
    assert re.search(r"^      additional_contexts:\n        model: \.\./sereno-model$", text,
                     flags=re.MULTILINE)
    sources = [line for line in instructions("COPY") if "--from=model" in line]
    assert any("artifacts/" in line and "model.joblib" in line for line in sources)
    assert any(re.search(r"\ssrc\s", line) for line in sources)


def test_the_image_runs_a_wsgi_server_as_an_unprivileged_user():
    users = instructions("USER")
    assert users and users[-1] not in ("root", "0")
    assert re.search(r"^RUN .*useradd .*" + re.escape(users[-1]), read("Dockerfile"),
                     flags=re.MULTILINE)
    commands = instructions("CMD")
    assert len(commands) == 1
    assert '"gunicorn"' in commands[0] and '"app:create_app()"' in commands[0]
    assert "flask" not in commands[0]
    assert re.fullmatch(r"python:3\.12\.\d+-slim-\w+", instructions("FROM")[0])


def test_the_image_holds_no_credentials():
    dockerfile = read("Dockerfile")
    assert not re.search(r"PASSWORD|DATABASE_URL|REDIS_URL|postgresql://|\.env", dockerfile)
    ignored = read(".dockerignore").splitlines()
    for entry in (".env", ".env.*", ".git", ".venv", "tests"):
        assert entry in ignored


def test_the_wsgi_server_is_pinned_like_the_other_dependencies():
    pins = read("requirements.txt").splitlines()
    assert any(re.fullmatch(r'gunicorn==\d+\.\d+\.\d+; sys_platform != "win32"', pin)
               for pin in pins)
