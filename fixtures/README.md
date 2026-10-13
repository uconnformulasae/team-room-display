# fixtures/

Test data for `pytest` and for looking at the display on a laptop (docs/TESTING.md).

**One file here is not just test data:** `events.schema.json` is the contract for
`web/data/events.json`. The admin app checks every publish against it, so the app won't
start without it. It lives here because PLAN.md §4 puts it here.

| File | What it is |
|---|---|
| `events.schema.json` | The events.json contract (used by the app and the tests) |
| `calendar_sample.ics` | A feed like Google Calendar's secret iCal link returns, dated around 2026-09-22: a weekly meeting with a skipped and a moved week, UTC and Pacific times, all-day and multi-day events, a cancelled one |
| `events_normal.json` | A believable week, frozen at Tue 2026-09-22 14:10 |
| `events_empty.json` | No events |
| `events_40.json` | 40 events in the next 30 days (list truncation) |
| `events_malformed.json` | Valid JSON in the wrong shape (the display must survive it) |
| `settings.json`, `photos.json` | The other two published files |
| `sample_photos/` | Placeholder photos used by `photos.json` and `scripts/seed_demo.py` |

The test images named in PLAN.md §4 (`img_12mp.jpg`, `img_30mb.jpg`, ...) are generated
when the tests run (`tests/helpers.py`, `make_test_images`), so they aren't in git.
