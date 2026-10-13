"""Set the shared admin password.

    python scripts/set_password.py
        Asks for the password and prints the ADMIN_PASSWORD_HASH=... line to paste into
        /etc/display/env (on the Pi) or .env (on your laptop).

    sudo /srv/display/.venv/bin/python scripts/set_password.py --write /etc/display/env
        Updates the file directly. If the file has no SECRET_KEY yet, a random one is added.

Afterwards restart the admin app: sudo systemctl restart display-admin
Changing the password does not log out people who are already logged in. To do that,
also delete the SECRET_KEY line and run this again with --write.

The password can also be piped in (one line on stdin) instead of typed.
"""
import argparse
import getpass
import os
import re
import secrets
import sys
import tempfile
from pathlib import Path

from werkzeug.security import generate_password_hash

MIN_LENGTH = 12


def read_password():
    if not sys.stdin.isatty():  # piped in, e.g. from a password manager's command-line tool
        return sys.stdin.readline().rstrip('\r\n')  # a file saved on Windows ends lines with \r\n
    first = getpass.getpass('New admin password: ')
    second = getpass.getpass('Type it again: ')
    if first != second:
        sys.exit('The two passwords did not match. Nothing was changed.')
    return first


def set_line(lines, key, value):
    """Replace the KEY=... line (or add one at the end)."""
    pattern = re.compile(rf'^\s*{re.escape(key)}\s*=')
    new_line = f'{key}={value}'
    for index, line in enumerate(lines):
        if pattern.match(line):
            lines[index] = new_line
            return
    lines.append(new_line)


def write_env_file(path, password_hash):
    lines = path.read_text(encoding='utf-8').splitlines() if path.exists() else []
    set_line(lines, 'ADMIN_PASSWORD_HASH', f"'{password_hash}'")  # quoted: the hash contains '$'
    if not any(re.match(r'^\s*SECRET_KEY\s*=', line) for line in lines):
        set_line(lines, 'SECRET_KEY', secrets.token_hex(32))
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o640
    # Write a temporary file next to it, then rename: the file is never left half-written.
    fd, temp_name = tempfile.mkstemp(dir=path.parent, prefix=f'.{path.name}.')
    with os.fdopen(fd, 'w', encoding='utf-8') as temp:
        temp.write('\n'.join(lines) + '\n')
    os.chmod(temp_name, mode)
    if path.exists() and hasattr(os, 'chown'):
        stat = path.stat()
        try:
            os.chown(temp_name, stat.st_uid, stat.st_gid)  # keep root:displayapp on the Pi
        except PermissionError:
            pass
    os.replace(temp_name, path)


def main():
    parser = argparse.ArgumentParser(description='Set the admin password for the team display.')
    parser.add_argument('--write', metavar='ENV_FILE', type=Path,
                        help='update this env file instead of printing the line (e.g. /etc/display/env)')
    args = parser.parse_args()

    password = read_password()
    if not password.strip():
        sys.exit('The password is blank. Nothing was changed.')
    if len(password) < MIN_LENGTH:
        sys.exit(f'Use at least {MIN_LENGTH} characters (a few random words works well). Nothing was changed.')
    password_hash = generate_password_hash(password)

    if args.write:
        try:
            write_env_file(args.write, password_hash)
        except OSError as error:
            sys.exit(f'Could not write {args.write}: {error.strerror}. Does that folder exist, and are you allowed '
                     'to write there (on the Pi: sudo)? Nothing was changed.')
        print(f'Updated {args.write}. Restart the admin app to use it (on the Pi: sudo systemctl restart display-admin).', file=sys.stderr)
    else:
        print(f"ADMIN_PASSWORD_HASH='{password_hash}'")


if __name__ == '__main__':
    main()
