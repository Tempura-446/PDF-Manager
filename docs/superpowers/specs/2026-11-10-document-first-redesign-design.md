# Document-first redesign: design

## Purpose

ZPDF's interface lists about 60 tools twice (a sidebar and a home screen of
cards), asks for the tool before the file, and keeps the tool list beside
an open document. The redesign starts from the document: open a PDF and
ZPDF shows it with the actions that fit it. The look moves to the layered
style the user chose from the mocks.

For the user and friends: people who open a PDF and want something done to
it. Success is: from a file on disk to "smaller", "searchable" or "signed"
without choosing from a list of 60, and nothing that worked before is lost.

Chosen mock: https://claude.ai/artifact/S3VidrpEAYpvYmbpyCfCKY

## The look (all stages)

Three layers: a softly tinted backdrop; the document and the working
surfaces on it; panels floating above with shadow. Frosted glass only on
surfaces that appear briefly (command palette, menus, toasts): text read
for longer stays on solid surfaces, as Windows' own guidance has it.

- Tokens stay CSS variables in `gui.html`, shared by `reader.css`. The
  existing names are kept (`--bg`, `--surface`, `--text`, `--accent`, …) so
  the reader and panels follow without edits; new ones are added:
  `--tint-a`, `--tint-b` (backdrop), `--glass` (frosted fill), `--float`
  (panel shadow), `--seal` (brand red, for the Z and the primary action
  only), `--teal` (selection, status, links), `--display` (file names).
- Colour values are the mock's, light and dark. `--accent` becomes the
  teal; primary buttons use `--seal`.
- Type: Instrument Sans for the interface, Instrument Serif for file
  names and the start screen's line, JetBrains Mono for file facts. The
  three are shipped with the app as files (no web requests: the app works
  offline and its content policy allows none) under their SIL Open Font
  Licence, listed in the third-party notices. Fallbacks: Segoe UI,
  Georgia, Consolas.
- Radii grow (panels 18, cards 14, controls 11). Motion: 150–450 ms, only
  to show cause and effect (a section opening, a result arriving, work in
  progress); none under "reduce motion".
- The web view can't use Windows' Mica, so the backdrop is ZPDF's own
  tint and doesn't follow the wallpaper.

## Stage 1: shell and start screen (one release)

**Rail.** The 268 px sidebar becomes a 60 px rail: the Z (start screen),
Read, Pages, Convert, Protect, All, and at the foot Settings and the
Update button when there is one. A rail button opens that group's tools
as a panel beside it (the existing `OPERATIONS` groups, mapped onto the
five). The rail keeps `class="tools"` and its `button[data-op]` for the
tools shown, so the end-to-end test's selectors still work.

**Search.** The sidebar's search box goes; the top bar has one search
field that opens the command palette (Ctrl+K and "/"), which already
finds every tool and every action on the open document. The palette gets
the frosted style.

**Start screen** (replaces Home): one line, a drop area, Recent files,
and "Start from several files" (Merge, Compare, Search a folder, plus the
other tools that take no single PDF, behind "More"). Pinned tools, when
there are any, sit above Recent. The grid of all tools is gone from the
start screen; "All" on the rail shows it.

**File facts.** Recent files show what ZPDF knows: pages, size, and the
flags that matter (scanned with no text, protected, signed, a form, a
drawing with a scale). New on the server:

- `facts(path, password=None) -> dict` in a new `pdfmanager/filefacts.py`:
  `{pages, size, protected, scanned, has_text, form_fields, signatures,
  tagged, pdfa, large_pictures}`. `scanned` is true when most pages are
  one full-page picture with no text of their own. Cheap by design: it
  looks at up to the first 10 pages and never renders.
- `/api/recent` adds `facts` to each entry, cached by path, size and
  modified time in the data folder so a start screen costs nothing the
  second time.
- `/api/facts/<id>` for an opened file.

Facts never fail a screen: an unreadable file shows its name and "can't
be read", a locked one "protected".

**Tool pages** keep their structure and ids; they take the new look. The
long help text on a tool page folds under "More about this".

## Stage 2: the file panel (one release)

With a document open, a panel floats at the right of the reader:

- The file's name and facts.
- **Suggested for this file**, from the facts, at most three:
  - scanned, no text → Make it searchable;
  - larger than 1 MB with pictures → Make it smaller, with the size
    Compress expects (a quick estimate from the pictures' resolution, said
    as "about");
  - a form that isn't fillable → Make it fillable (the reader's existing
    check);
  - unsigned and has a signature line or field → Sign;
  - not PDF/A and older than a year → no suggestion (not a need people
    have unprompted).
- **Or do something else**: six common tools, and "All tools".
- Choosing a tool opens its options in the panel (the same form
  `renderForm` builds, mounted in the panel) with Run. Tools that need
  the page (crop, redact, bookmarks, the page board) switch to their
  existing full view, as today.
- The result opens as a new reader tab beside the original and a toast
  says what was saved; nothing is downloaded unless asked.

On the server, `/api/run` accepts `ids` (opened files) in place of
uploaded bytes, so a 200 MB file isn't sent back to the server it came
from. The result is registered as an opened file and its id returned.

The reader's own left sidebar (pages, bookmarks, comments) and its bars
stay. The panel can be closed and reopened (a button in the reader's
toolbar, and "T", which today opens the tools tray: the tray is replaced
by the panel). Below 1100 px wide the panel overlays the page instead of
sitting beside it.

## Stage 3: polish (one release)

- Motion and toasts throughout, as in the mock.
- The pages for tools that start from several files get the drop-first
  layout of the start screen.
- The two recent-file lists (start screen and the reader's empty state)
  become one.

## What stays

Every tool, option, shortcut and endpoint. The reader's features.
Settings, updates, themes (System, Light, Dark). The command palette's
behaviour. Tool ids, so recipes and pinned tools carry over.

## Tests

- `tests/test_filefacts.py`: each fact on a file made for it (scan with
  and without text, protected, signed, form, tagged, PDF/A), the hostile
  files from `badpdfs.py` (never an exception), and the cache.
- Server: `/api/recent` carries facts; `/api/run` with `ids` runs on an
  opened file and returns a result id; an id that isn't open is refused.
- The page, in a browser (Playwright, skipped where there is none): the
  rail shows five groups; a tool opens from the rail, the palette and the
  start screen; a dropped PDF opens in the reader; in stage 2 a
  suggestion runs and its result opens as a second tab.
- The existing checks on `gui.html` keep passing: the `ICONS` and
  `TAGLINES` literals, the brand mark, the end-to-end selectors.
- Light and dark screenshots of the start screen, a tool page and the
  reader with its panel are looked at before each stage ships.

## Not in this design

- An assistant or anything that sends a document anywhere.
- Changing what any tool does.
- A mobile layout beyond what exists.
