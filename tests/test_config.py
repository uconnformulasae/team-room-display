"""Configuration comes from the environment, falling back to /etc/display/env or .env."""
from app import REPO_ROOT, create_app, load_config, read_env_file


def test_env_file_is_parsed_like_systemd_does(tmp_path):
    env = tmp_path / 'env'
    env.write_text(
        '# comment line\n'
        '\n'
        'SECRET_KEY=abc123\n'
        "ADMIN_PASSWORD_HASH='scrypt:32768:8:1$salt$hash'\n"
        'DISPLAY_DB="/srv/display/data/display.db"\n'
        '  DISPLAY_WEB = /srv/display/web  \n'
        'CALENDAR_ICS_URL=https://calendar.google.com/calendar/ical/team%40group.calendar.google.com/private-XXXX/basic.ics\n'
        'DRIVE_FOLDER_URL=https://drive.google.com/drive/folders/XXXX?usp=sharing\n'
        'DRIVE_API_KEY=XXXX\n'
    )

    assert read_env_file(env) == {
        'SECRET_KEY': 'abc123',
        'ADMIN_PASSWORD_HASH': 'scrypt:32768:8:1$salt$hash',  # '$' kept literally
        'DISPLAY_DB': '/srv/display/data/display.db',
        'DISPLAY_WEB': '/srv/display/web',
        'CALENDAR_ICS_URL': 'https://calendar.google.com/calendar/ical/team%40group.calendar.google.com/private-XXXX/basic.ics',
        'DRIVE_FOLDER_URL': 'https://drive.google.com/drive/folders/XXXX?usp=sharing',  # '?' and '=' kept
        'DRIVE_API_KEY': 'XXXX',
    }


def test_real_environment_variables_win_over_the_file(tmp_path, monkeypatch):
    env = tmp_path / 'env'
    env.write_text('SECRET_KEY=from-file\nDISPLAY_WEB=/from/file\n')
    monkeypatch.setenv('DISPLAY_ENV_FILE', str(env))
    monkeypatch.setenv('SECRET_KEY', 'from-environment')
    monkeypatch.delenv('DISPLAY_WEB', raising=False)

    config = load_config()

    assert config['SECRET_KEY'] == 'from-environment'
    assert config['DISPLAY_WEB'] == '/from/file'


def test_defaults_point_into_the_repo_when_nothing_is_configured(tmp_path, monkeypatch):
    monkeypatch.setenv('DISPLAY_ENV_FILE', str(tmp_path / 'missing'))
    for key in ('SECRET_KEY', 'ADMIN_PASSWORD_HASH', 'DISPLAY_DB', 'DISPLAY_WEB', 'CALENDAR_ICS_URL',
                'DRIVE_FOLDER_URL', 'DRIVE_API_KEY'):
        monkeypatch.delenv(key, raising=False)

    config = load_config()

    assert config['DISPLAY_DB'] == str(REPO_ROOT / 'data' / 'display.db')
    assert config['DISPLAY_WEB'] == str(REPO_ROOT / 'web')
    assert config['SECRET_KEY'] is None
    assert config['CALENDAR_ICS_URL'] is None
    assert config['DRIVE_FOLDER_URL'] is None and config['DRIVE_API_KEY'] is None


def test_missing_secret_key_falls_back_to_a_temporary_one(tmp_path, monkeypatch):
    monkeypatch.setenv('DISPLAY_ENV_FILE', str(tmp_path / 'missing'))
    monkeypatch.delenv('SECRET_KEY', raising=False)

    app = create_app({'DISPLAY_DB': str(tmp_path / 'd.db'), 'DISPLAY_WEB': str(tmp_path / 'web')})

    assert len(app.config['SECRET_KEY']) >= 32
    assert app.config['SECRET_KEY_IS_TEMPORARY'] is True


def test_export_lines_are_read_too(tmp_path):  # L7
    env = tmp_path / 'env'
    env.write_text('export SECRET_KEY=abc\nexport DISPLAY_WEB="/w"\n')

    assert read_env_file(env) == {'SECRET_KEY': 'abc', 'DISPLAY_WEB': '/w'}
