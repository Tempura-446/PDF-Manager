# Security

## Reporting a vulnerability

Please don't report a security problem in a public issue. Report it
privately instead, through GitHub's private vulnerability reporting:

1. Go to the repository's **Security** tab
   (<https://github.com/Tempura-446/PDF-Manager/security>).
2. Choose **Report a vulnerability**, and describe the problem: what it
   is, the ZPDF version, how to make it happen (a sample file helps, if
   it's safe to share), and what someone could do with it.

Only the owner sees the report. You'll get an answer there, usually within
a week, and the fix goes into the next release. You're credited in the
changelog if you want to be.

(For the owner: the button only appears once private vulnerability
reporting is turned on, in the repository's **Settings › Code security ›
Private vulnerability reporting**.)

## Supported versions

Only the latest release, from
[ZPDF-releases](https://github.com/Tempura-446/ZPDF-releases/releases),
gets security fixes. The app's **Update** button installs it. Older
versions are not fixed; update first and check the problem is still there.

## In scope

- Opening a PDF, image, Office or other file in ZPDF that makes it run
  code, read or write files it shouldn't, or hang or crash in a way a
  hostile file could use.
- The app's built-in server (on `127.0.0.1`, with its token): anything
  that lets another program or a web page in a browser use it without the
  token.
- Passwords, encryption, redaction, removing hidden data and digital ID
  signatures that don't do what they say: text left behind after
  redaction, a weak or wrong encryption, a signature shown as valid that
  isn't.
- The installer and the Update button: anything that would install a
  release that isn't the owner's, or a changed package.
- Leaks of your documents or their names, for example into the log or the
  diagnostics ZPDF copies.

## Not in scope

- Problems in other software ZPDF uses (pdf.js, pypdf, Python, Windows,
  Tesseract, Ghostscript, LibreOffice and so on): report those to their
  makers. If ZPDF uses one in an unsafe way, that is in scope.
- Someone who already controls your computer or your user account.
- Copies of ZPDF changed by someone else, or not downloaded from
  ZPDF-releases.
- The "Windows protected your PC" warning: the app isn't code-signed yet.
