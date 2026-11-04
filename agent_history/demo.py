"""Invented conversations for screenshots and videos.

Everything here is fiction: the projects (Lumen Notes, Harbor API, Fieldbook) and the
people do not exist. Demo mode inserts only these rows into a temporary index, so real
histories are never opened. Good words to type: pricing, retry, migration, flaky, deploy.
"""
import time
import uuid

DAY_MS = 24 * 60 * 60 * 1000
NAMESPACE = uuid.UUID('6f1d3c52-5a0e-4c57-9a55-0d3b8f6f2a11')

# (agent, days ago, title, [user, assistant, user, assistant...])
CONVERSATIONS = (
    ('claude', 0.1, 'Pricing page for Lumen Notes', (
        'Where did we land on pricing for Lumen Notes? I remember two tiers and an annual discount.',
        'We settled on two tiers: Free with 3 notebooks, and Plus at $6 per month or $60 per year. '
        'The annual price is the discount, so there is no separate coupon to maintain. '
        'Team pricing stays out of the launch and goes on the roadmap for the second quarter.',
        'Good. Write the pricing table with the annual price selected by default.',
        'Done. The pricing table in src/pages/pricing.tsx now opens on the annual plan and shows '
        '"$5 per month, billed yearly" with the monthly price as the secondary option.')),
    ('codex', 0.4, 'Retry logic for the webhook sender', (
        'The webhook sender gives up after the first timeout. Add a retry with backoff.',
        'Added exponential backoff with jitter: 1 s, 2 s, 4 s, 8 s, then the delivery moves to the '
        'dead-letter table. A retry only happens on timeouts and 5xx answers, because a 4xx will '
        'not fix itself. The attempt count is stored on the delivery row so a restart keeps it.')),
    ('claude', 1.2, 'Flaky checkout test in CI', (
        'test_checkout_total is flaky again, it fails about one run in ten.',
        'The flaky failure comes from a shared clock. Two tests freeze time in the same module, and '
        'pytest-xdist can run them in the same worker, so the tax rate lookup sometimes reads the '
        'wrong day. I moved the freeze into a fixture scoped to each test and ran the file 200 times '
        'with no failures.',
        'Can we stop this class of problem from coming back?',
        'Yes. I added a lint rule that rejects module-level time freezing, and a note in '
        'CONTRIBUTING.md explaining the fixture to use instead.')),
    ('opencode', 1.8, 'Postgres migration for tags', (
        'Plan the migration that moves note tags from a text array to a join table.',
        'The migration runs in three steps so it can ship without downtime. First create the tags '
        'and note_tags tables. Then backfill in batches of 5,000 rows while the app writes to both. '
        'Finally switch reads to the join table and drop the old column one release later.',
        'What happens if the backfill stops halfway?',
        'It can restart safely. Each batch records the last note ID in migration_state, and the '
        'inserts use ON CONFLICT DO NOTHING, so running a batch twice changes nothing.')),
    ('pi', 2.3, 'Deploy checklist for Harbor API', (
        'Give me the deploy checklist we used for Harbor API last time.',
        'The deploy checklist was: run the database migration first, deploy one canary instance, '
        'watch the error rate for ten minutes, then roll out the rest. Rollback is the previous '
        'image tag, because the migration is always backward compatible with the last release.')),
    ('omp', 2.9, 'Search box feels slow', (
        'The note search box feels slow once there are thousands of notes.',
        'Each key press was sending a request and rendering the whole list. I added a 120 ms '
        'debounce, cancelled stale requests, and capped the list at 50 rows. Typing now stays '
        'under 16 ms per frame on the large test account.')),
    ('claude', 3.5, 'Rate limit design for the public API', (
        'What rate limit should the public API use?',
        'I suggest a token bucket per API key: 60 requests per minute with a burst of 20. Answer '
        '429 with a Retry-After header, so a client can retry at the right moment. Keep the counters '
        'in Redis with a two-minute expiry, which keeps memory bounded.',
        'And for clients without a key?',
        'Limit by IP at 10 requests per minute. It is deliberately low, because a key is free.')),
    ('pocket', 4.1, 'Idea: weekly digest email', (
        'Idea from the train: a weekly digest of notes you have not opened in a while.',
        'Saved. A first version could pick five stale notes every Monday morning and link back to '
        'each one. It needs an unsubscribe link from day one, and the send time should follow the '
        'time zone of the user, not of the server.')),
    ('codex', 4.8, 'Sync conflict when two devices edit a note', (
        'Two devices editing the same note offline lose changes. How do we fix that?',
        'Last write wins is the cause. I replaced it with a three-way merge against the last synced '
        'version. Edits in different paragraphs merge without asking; edits in the same paragraph '
        'keep both copies and mark the note as a conflict, so nothing is lost silently.')),
    ('claude', 5.6, 'Onboarding emails', (
        'Draft the onboarding emails for new Lumen Notes accounts.',
        'Three emails. Day 0: how to capture a first note. Day 2: import from another app. Day 7: '
        'notebooks and sharing. Each one has a single button and stays under 80 words. The Plus '
        'pricing appears only in the last email, and only as a footer line.')),
    ('opencode', 6.4, 'Image upload fails on large files', (
        'Uploads over 10 MB fail with a 413 in production but work locally.',
        'The app accepts 25 MB, but the proxy in front of it has client_max_body_size set to 10m. '
        'Local runs skip that proxy. I raised the proxy limit to 25m and added a check in the '
        'browser, so a file that is too large shows a clear message before the upload starts.')),
    ('pi', 7.2, 'Dark mode colors', (
        'The dark mode looks washed out on the editor screen.',
        'The text used 60% opacity on a dark gray background, which gives a contrast ratio of 3.9. '
        'I moved the colors to tokens and raised body text to 7.2. Links keep the blue accent, '
        'slightly lighter in dark mode so they stay readable.')),
    ('omp', 8.1, 'Fieldbook CSV export', (
        'Add a CSV export to Fieldbook. Dates keep breaking in spreadsheets.',
        'The export now writes ISO 8601 dates and a UTF-8 byte order mark, which spreadsheet apps '
        'need to detect the encoding. Cells starting with =, + or @ get a leading apostrophe, so a '
        'field can never run as a formula.')),
    ('claude', 9.3, 'Memory leak in the sync worker', (
        'The sync worker grows by about 200 MB a day until it restarts.',
        'A heap snapshot pointed at the retry queue: finished jobs stayed referenced by a closure '
        'in the logger. Removing the closure keeps memory flat at 140 MB over a 48-hour soak test. '
        'I also added a memory graph to the worker dashboard.')),
    ('codex', 10.5, 'Deploy preview for each pull request', (
        'Set up a deploy preview for each pull request.',
        'Each pull request now gets a preview URL from CI, with a seeded database and a banner '
        'marking the environment. Previews stop after 48 hours without commits to keep the cost '
        'down. The deploy step posts the link as a check on the pull request.')),
    ('pocket', 11.7, 'Pricing feedback from the beta group', (
        'Summary of what the beta group said about pricing, please.',
        'Most testers found $6 per month fair. The main objection was the 3 notebook limit on the '
        'free tier, not the price. Two teachers asked for an education discount, which is worth a '
        'note for later.')),
    ('claude', 12.4, 'Accessibility pass on the editor', (
        'Do an accessibility pass on the editor toolbar.',
        'All toolbar buttons now have labels and visible focus rings, and arrow keys move between '
        'them as one group. Screen readers announce formatting changes through a polite live '
        'region. Remaining gap: the table picker is still mouse only.')),
    ('opencode', 13.6, 'Feature flags without a vendor', (
        'Do we need a feature flag service yet?',
        'Not yet. A flags table with a five-minute cache covers the current twelve flags. A service '
        'starts paying off once non-engineers need to change flags or you need percentage rollouts '
        'per segment, so that is the point to review the decision.')),
)


def session_id(agent, title):
    return str(uuid.uuid5(NAMESPACE, agent + ':' + title))


def messages(now_ms=None):
    """-> [(host, session, title, role, timestamp_ms, text)], with dates relative to now."""
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    rows = []
    for agent, days_ago, title, turns in CONVERSATIONS:
        start = now_ms - int(days_ago * DAY_MS)
        for number, body in enumerate(turns):
            role = 'user' if number % 2 == 0 else 'assistant'
            rows.append((agent, session_id(agent, title), title, role, start + number * 90_000, body))
    return rows
