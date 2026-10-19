# Memory reviews eval (M22-15)

Do the weekly, monthly and yearly Memory reviews (docs/MEMORY.md §4.9)
summarize and curate the right things on the real model? Each repetition
has two throwaway users with `write_file`, `edit_file` and `delete`
allowed and a timezone set. Dates are relative to the day it runs, so the
periods are the ones a first review covers: the last full week, last
month and last year.

The main user (`e2e-reveval-*`) is seeded with:

- day files for four days of the last full week (a trip to Bend booked,
  a bouldering class started, a dentist checkup, a dinner) and four days
  of last month (a half marathon run, a long training run, the dog,
  Priya's promotion);
- month files for three months of last year (moved to Portland, adopted
  Biscuit, a new job);
- pages, including two for the same person (`people/priya.md` and
  `people/priya-shah.md`, both linked), a pottery page last dated over a
  year ago, and a bouldering page that's active;
- a `memory.md` whose Current plans has two past plans (the half
  marathon, the dentist) and two future ones (the Bend trip, a birthday).

Then Run now on the weekly, monthly and yearly reviews, in that order.
The quiet user (`e2e-revevalq-*`) has a `memory.md` and one day file three
weeks before the reviewed week; it gets one weekly review.

Cases, each pass/fail per repetition:

- **week_file / month_file / year_file**: the period's file exists, links
  down to at least one of the files below it, and mentions all but at
  most one of the period's key things.
- **lately**: after the weekly review, `memory.md` has a `## Lately`
  section of at most 1,200 characters that links the week file.
- **past_plans**: after the weekly review, Current plans has neither past
  plan and both future ones. (The report also says what was left after
  the monthly review.)
- **duplicates**: after the monthly review, one Priya page is left and no
  link is broken. (Also reported after the yearly one.)
- **stale**: after the monthly review the pottery page, and only it, has
  "Not mentioned since …"; the bouldering page never gets it.
- **size**: `memory.md` is under 6,000 characters after each review.
- **links**: after the yearly review, every `file:` link in the notes
  points to a file that exists.
- **invented**: no capitalized word in mid-sentence (a name, usually) in
  what the reviews wrote that isn't in the seeded notes, apart from
  calendar names and headings.
- **empty_period**: the quiet user's weekly review succeeds and changes
  no file.

Also reported, not scored: whether each run succeeded and linked its
changes, any chat, app-row, web or integration tool a run used, and which
files each review changed. The checks are coarse: read the notes in the
report too.

## Run

Needs the live stack. Takes `/tmp/homeai-stack.lock` itself; about 10
minutes per repetition.

```bash
scripts/eval/memory_reviews/run.sh
EVAL_RUNS=1 scripts/eval/memory_reviews/run.sh
```

Deletes its users on exit. Report:
`scripts/eval/memory_reviews/last-report.json` (gitignored), with each
repetition's notes after seeding and after each review.
