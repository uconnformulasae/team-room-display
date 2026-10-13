"""The wording rules from docs/SPEC.md §3 and §4: what the screen says for "in 2 h",
"Tomorrow", "6:30 – 8 PM", "Oct 2 – 4", and so on. Each case calls the page's own
functions (web/app.js) in the browser, so the table here IS the spec."""
import pytest

pytest.importorskip('playwright.sync_api')


def event(start, end=None, all_day=False):
    return {'title': 'x', 'start': start, 'end': end, 'all_day': all_day, 'location': ''}


@pytest.mark.parametrize('now, ev, expected', [
    ('2026-09-22T14:10', event('2026-09-22T13:00', '2026-09-22T17:00'), 'Now'),
    ('2026-09-29T10:00', event('2026-09-29T00:00', all_day=True), 'Today'),
    ('2026-10-02T10:00', event('2026-10-02T00:00', '2026-10-04T00:00', all_day=True), 'Through Sunday'),
    ('2026-09-22T14:10', event('2026-09-22T14:10'), 'Starting now'),
    ('2026-09-22T14:10', event('2026-09-22T14:35'), 'in 25 min'),
    ('2026-09-22T14:10', event('2026-09-22T16:25'), 'in 2 h 15 min'),
    ('2026-09-22T14:10', event('2026-09-22T16:10'), 'in 2 h'),
    ('2026-09-22T14:10', event('2026-09-22T19:50'), 'in 5 h'),
    ('2026-09-22T23:00', event('2026-09-23T09:00'), 'Tomorrow'),
    ('2026-09-22T14:10', event('2026-09-23T00:00', all_day=True), 'Tomorrow'),
    ('2026-09-22T14:10', event('2026-09-24T18:30'), 'Thursday'),
    ('2026-09-22T14:10', event('2026-10-15T18:30'), 'in 23 days'),
])
def test_relative_labels(screen, now, ev, expected):
    screen.open()

    assert screen.call('relativeLabel(readEvent(a), parseLocal(b))', ev, now) == expected


@pytest.mark.parametrize('now, ev, expected', [
    ('2026-09-22T14:10', event('2026-09-24T18:30', '2026-09-24T20:00'), '6:30 – 8 PM'),
    ('2026-09-22T14:10', event('2026-09-24T11:00', '2026-09-24T13:00'), '11 AM – 1 PM'),
    ('2026-09-22T14:10', event('2026-09-25T23:59'), '11:59 PM'),
    ('2026-09-22T14:10', event('2026-10-02T17:00', '2026-10-04T14:00'), '5 PM – Sun'),
    ('2026-09-22T14:10', event('2026-09-22T13:00', '2026-09-22T17:00'), 'Until 5 PM'),
    ('2026-09-22T14:10', event('2026-09-21T09:00', '2026-09-25T17:00'), 'Until Fri'),
    ('2026-09-22T14:10', event('2026-09-29T00:00', all_day=True), 'All day'),
    ('2026-09-22T14:10', event('2026-10-02T00:00', '2026-10-04T00:00', all_day=True), 'Oct 2 – 4'),
    ('2026-09-22T14:10', event('2026-10-30T00:00', '2026-11-02T00:00', all_day=True), 'Oct 30 – Nov 2'),
])
def test_list_time_column(screen, now, ev, expected):
    screen.open()

    assert screen.call('listTime(readEvent(a), parseLocal(b))', ev, now) == expected


@pytest.mark.parametrize('ev, expected', [
    (event('2026-09-24T18:30', '2026-09-24T20:00'), 'Thu, Sep 24 · 6:30 – 8 PM'),
    (event('2026-09-25T23:59'), 'Fri, Sep 25 · 11:59 PM'),
    (event('2026-09-29T00:00', all_day=True), 'Tue, Sep 29 · All day'),
    (event('2026-10-02T00:00', '2026-10-04T00:00', all_day=True), 'Fri, Oct 2 – Sun, Oct 4'),
    (event('2026-10-02T17:00', '2026-10-04T14:00'), 'Fri, Oct 2, 5 PM – Sun, Oct 4, 2 PM'),
])
def test_absolute_when(screen, ev, expected):
    screen.open()

    assert screen.call('absoluteWhen(readEvent(a))', ev) == expected


