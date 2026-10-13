"""Facts about the Pi for the admin Status page, and the two buttons that act on it.

Every command here is fixed in the code; nothing a user types ever reaches a shell. The
two commands that need root go through sudo, and /etc/sudoers.d/display allows exactly
those two (pi/sudoers-display). On a laptop the Pi-only commands don't exist, and the
page shows "unknown" instead.
"""
import json
import shutil
import subprocess
import threading
import time
from pathlib import Path

from . import clock

SERVICES = ('nginx', 'display-admin', 'tailscaled')


def run(command, timeout=5):
    """Run a command. Returns (exit status, output); the status is None if the command
    doesn't exist on this machine or timed out."""
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as error:
        return None, str(error)
    return result.returncode, (result.stdout.strip() or result.stderr.strip())


# --- The two commands that run as root ---------------------------------------------
# The only place the app uses sudo. /etc/sudoers.d/display (pi/sudoers-display) allows
# exactly these two commands, with no arguments.

RESTART_DISPLAY = ['sudo', '-n', '/usr/local/bin/display-restart']  # kills Chromium; kiosk.sh restarts it
REBOOT = ['sudo', '-n', '/sbin/reboot']


def restart_display():
    """Close Chromium; kiosk.sh opens it again within a few seconds. Returns (ok, output)."""
    status, output = run(RESTART_DISPLAY, timeout=15)
    return status == 0, output


def reboot_soon(delay_s=2):
    """Reboot after a short pause, so the page saying "rebooting" gets back to the phone."""
    def reboot_later():
        time.sleep(delay_s)
        run(REBOOT, timeout=30)
    threading.Thread(target=reboot_later, daemon=True).start()


# --- Facts about the Pi, for the Status page ------------------------------------------

def read_uptime_seconds():
    try:
        return float(Path('/proc/uptime').read_text().split()[0])
    except (OSError, ValueError, IndexError):
        return None


def describe_uptime(seconds):
    if seconds is None:
        return None
    days, rest = divmod(int(seconds), 86400)
    hours, rest = divmod(rest, 3600)
    if days:
        return f'{days} day{"s" if days != 1 else ""}, {hours} h'
    return f'{hours} h {rest // 60} min'


def describe_disk(path):
    try:
        usage = shutil.disk_usage(path)
    except OSError:
        return None, False
    free_gb, total_gb = usage.free / 1e9, usage.total / 1e9
    return f'{free_gb:.1f} GB free of {total_gb:.0f} GB', usage.free < 1e9


def cpu_temperature():
    status, output = run(['vcgencmd', 'measure_temp'])
    if status == 0 and output.startswith('temp='):
        return output[5:].replace("'C", ' °C')
    try:
        return f"{int(Path('/sys/class/thermal/thermal_zone0/temp').read_text()) / 1000:.1f} °C"
    except (OSError, ValueError):
        return None


def describe_throttled(code):
    """Turn `vcgencmd get_throttled` (e.g. 0x50005) into words. The bits are documented at
    https://www.raspberrypi.com/documentation/computers/os.html#get_throttled"""
    try:
        value = int(code, 16)
    except ValueError:
        return f'Unknown ({code})'
    if value == 0:
        return 'No power problems'
    problems = []
    if value & 0x1:
        problems.append('Under-voltage right now: use the official power supply')
    if value & 0xC:
        problems.append('Slowed down right now (too hot, or not enough power)')
    if value & 0x10000 and not value & 0x1:
        problems.append('Under-voltage has happened since boot: check the power supply')
    if value & 0x40000 and not value & 0xC:
        problems.append('Was slowed down at some point since boot')
    return '; '.join(problems) or f'Unusual value {code}'


def power_status():
    status, output = run(['vcgencmd', 'get_throttled'])
    if status != 0 or not output.startswith('throttled='):
        return None
    return describe_throttled(output.split('=', 1)[1])


def last_publish(web_dir):
    """(generated_at of events.json as 'YYYY-MM-DD HH:MM', minutes ago), or (None, None)."""
    try:
        stamp = json.loads((Path(web_dir) / 'data' / 'events.json').read_text())['generated_at']
        minutes = int((clock.now() - clock.parse(stamp)).total_seconds() // 60)
    except (OSError, ValueError, KeyError, TypeError):
        return None, None
    return stamp[:16].replace('T', ' '), minutes


def last_backup(data_dir):
    """What scripts/backup.sh wrote to last_backup.json: {'at', 'ok', 'message'}, or None."""
    try:
        result = json.loads((Path(data_dir) / 'last_backup.json').read_text())
        return {'at': str(result['at'])[:16].replace('T', ' '), 'ok': result['ok'] is True,
                'message': str(result.get('message', ''))}
    except (OSError, ValueError, KeyError, TypeError):
        return None


def service_states():
    states = {}
    for name in SERVICES:
        status, output = run(['systemctl', 'is-active', name])
        states[name] = output if status is not None and output else 'unknown'
    return states


def chromium_running():
    status, _ = run(['pgrep', '-x', 'chromium'])
    return {0: True, 1: False}.get(status)  # None: can't tell (no pgrep here)


def gather(config):
    """Everything the Status page shows."""
    published, published_minutes = last_publish(config['DISPLAY_WEB'])
    disk, disk_low = describe_disk(config['DISPLAY_WEB'])
    return {
        'published': published,
        'published_minutes': published_minutes,
        'backup': last_backup(Path(config['DISPLAY_DB']).parent),
        'chromium': chromium_running(),
        'services': service_states(),
        'uptime': describe_uptime(read_uptime_seconds()),
        'disk': disk,
        'disk_low': disk_low,
        'temperature': cpu_temperature(),
        'power': power_status(),
    }
