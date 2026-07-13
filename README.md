# automated-shorts

Turns Reddit stories into narrated vertical videos (the TikTok "reddit story
over gameplay footage" format), with an LLM deciding which stories are
actually worth making. One command crawls a subreddit, scores every candidate
post, rewrites weak endings, and renders finished 1080x1920 videos with
burned-in captions and ready-to-paste upload text.

The video rendering is the boring part. The point of the project is the
curation layer: most posts on any given day are mediocre, and a bot that
posts mediocre content is worthless. So everything runs through a judge that
scores candidates and refuses to produce anything below a quality bar.

## How it works

```
                    +----------------------------------------------------------+
                    |                       curate.py                          |
                    |                                                          |
  Reddit  --------->|  1. INGEST          2. JUDGE            3. SCRIPT DOCTOR |
  (OAuth API or     |  fetch listing +    scores every        rewrites flat    |---+
   RSS fallback)    |  top comments       candidate 0-100,    endings, fixes   |   |
                    |  (rate limited)     gates on threshold  grammar, swaps   |   |
                    |                                         banned words     |   |
                    |                                                          |   |
                    |                     4. writes TikTok/YouTube captions    |   |
                    +----------------------------------------------------------+   |
                                                                                    |
                    +----------------------------------------------------------+   |
                    |                        main.py                           |   |
                    |               renders each story that passed             |<--+
                    |                                                          |
   narrate.py  ---->|   TTS voiceover, word-timed captions (edge-tts)          |
   post_card.py --->|   fake Reddit post card PNG for the intro                |
   build_video.py ->|   ffmpeg: card overlay, whoosh, captions over footage    |
                    +----------------------------------------------------------+
                                              |
                                              v
                             demo/<sub>-<stamp>-N.mp4  +  .upload.json
```

Every stage is its own module with a CLI, so each step can be run and
debugged alone. `main.py` chains them.

## Finding stories

The bot doesn't search the internet. It draws from a hand-picked pool of
subreddits that reliably produce narratable stories (confession subs, AITA
subs, tifu, nosleep, LetsNotMeet, AskReddit). Each run either takes a
subreddit you name or rolls a weighted random one from the pool.

From there the funnel is:

1. Pull the top posts for a time window (day by default, up to all time).
   Reddit's own upvote ranking is the first quality filter, for free.
2. Drop anything already used (post IDs live in a seen file, so a story can
   never become two videos), anything deleted or stickied, and anything
   outside the narration length window, roughly 60 to 120 seconds read
   aloud.
3. Fetch the top comments for the survivors. Comments are how the judge
   reads the room: real audience shock or laughter counts as evidence.
4. Judge and gate (next section).

AskReddit works differently because the post is just a question. There the
top comment becomes the story ("doctors, what deathbed confession stuck with
you?" is a question, the answer is the content), and the remaining comments
stay on as the audience signal.

## Scoring

Each candidate gets a structured scorecard from Claude: an overall 0-100
score, sub-scores for hook, emotional intensity, narratability, and audience
signal (how strongly the post's real comment thread reacted), a category, a
"needs ending fix" flag, and a one-line reason.

The overall score is a judgment call by the model, not a weighted average of
the sub-scores. The rubric puts hard rules on it: a story that opens slow
gets capped below 50 no matter how good the rest is, because viewers decide
in the first few seconds. A weak ending does not lower the score, since the
script doctor can fix endings, it just sets the flag. Unsafe content gets
zeroed. If nothing clears the bar (default 65), the run skips instead of
producing a dud. Scores drift a few points between runs since it's a
judgment, and the threshold accounts for that.

There's also a feedback loop, two layers of it. The web UI lets you rate
finished videos 1-5 with a note; ratings go to `ratings.json`, and every
later run builds a taste profile from them (per-subreddit track record,
plus examples of what got rated up or down and why) that gets prepended to
the judge's prompt and the script doctor's prompt. Not fine-tuning, just a
memory file, but it means the pipeline gets more aligned with what you
actually want the more you rate.

The second layer checks the judge against reality. After posting a video
you enter its actual numbers (views, likes, comments, completion rate) in
the UI. The analysis view joins those with the judge's scores and computes
the correlation between judged score and each metric, which answers the
question that matters: does a judged-88 actually outperform a judged-66?
Real results also feed back into the judge's prompt, weighted above the
rubric, so subreddits and story shapes that perform well in practice get
favored over ones that only look good on paper.

## Model choices

Scoring and caption writing run on Sonnet, which is cheap enough to score a
whole listing for a fraction of a cent and good enough for judgment work.
The script doctor runs on Opus with thinking enabled, because rewriting an
ending so the twist actually lands is the one real writing task in the
pipeline and it only runs once per posted story. A day's batch costs a few
cents total.

The script doctor does more than endings: it smooths broken English into
something a narrator can read, expands abbreviations (AITAH becomes "am I
the asshole", 1800 becomes "6pm"), and swaps words that trip platform
moderation for the substitutes the genre uses. The intro card keeps the
original title for authenticity, the narrator reads the polished one.

## Reddit access

Reddit blocks anonymous JSON scraping (the old `.json` endpoints return 403
from most IPs now), and creating an API app is gated behind an approval
process. So the fetcher has two paths and picks automatically:

- With `REDDIT_CLIENT_ID`/`REDDIT_CLIENT_SECRET` set, it uses the official
  OAuth API at oauth.reddit.com. Fast (100 requests/min) and includes vote
  counts.
- Without credentials, it falls back to Reddit's public RSS/Atom feeds,
  which still work unauthenticated. `r/<sub>/top/.rss` returns the listing
  with full post text, and each post's own `.rss` returns its comments. The
  catch is rate limiting: roughly one request per minute before 429s, so
  the fetcher self-paces and backs off. A crawl of one listing plus
  comments for 8 candidates takes about 9 minutes.

RSS feeds carry no vote counts, but the top-of-day feed is already ordered
by Reddit's own ranking, so position substitutes for score, and the judge
works from story text and comment text anyway. Both paths return the same
shape to the rest of the pipeline.

The slow crawl gets amortized: one crawl produces every story that clears
the bar, up to a cap, not just the best one.

## Modules

| Module | Does |
|---|---|
| `reddit_fetch.py` | listings + comments, OAuth or paced RSS fallback |
| `curate.py` | judging, threshold gate, script doctor, upload copy |
| `narrate.py` | TTS + word-timed .ass captions, one word at a time |
| `post_card.py` | fake Reddit post card PNG (invented user, awards, verified badge) |
| `build_video.py` | ffmpeg compositing, card overlay + whoosh + captions |
| `main.py` | runs the whole thing |
| `web/server.py` | local FastAPI console: generate, watch progress, rate videos |
| `feedback.py` | ratings store + taste profile for the judge and doctor |
| `metrics.py` | real platform stats, judge-vs-reality correlation, performance memory |

## Running it

You need:

- Python 3.11+ and ffmpeg on PATH
- an Anthropic API key with credit on it (console.anthropic.com, set it as
  `ANTHROPIC_API_KEY`). Usage is pay-per-token; a full batch run costs a few
  cents, so even the $5 minimum credit lasts a long time
- a background gameplay video (any long landscape mp4 works, it gets
  center-cropped to vertical)

```bash
pip install -r requirements.txt

# crawl a random subreddit from the pool, render up to 3 videos
python main.py --background backgrounds/parkour.mp4 --out-dir demo

# or pick the subreddit and go deeper in time
python main.py nosleep --time-filter week --background backgrounds/parkour.mp4 --out-dir demo

# or use the web console
python -m uvicorn web.server:app --port 8000
```

Each video lands next to a `.upload.json` with platform captions and AI
disclosure flags, and a `.meta.json` with the full scorecard. If nothing
clears the bar the run exits clean.

Optional: `REDDIT_CLIENT_ID` / `REDDIT_CLIENT_SECRET` for the fast OAuth
path, `REDDIT_RSS_INTERVAL` to tune RSS pacing.

## Stack

Python, Anthropic API (structured outputs, thinking, two models routed by
task), edge-tts, ffmpeg/libass, Pydantic, FastAPI, Reddit API + RSS.

## Notes

Personal-scale project. Reddit stories and gameplay footage sit in the usual
gray areas of this genre; anything commercial would need licensed footage
and a content-rights pass.
