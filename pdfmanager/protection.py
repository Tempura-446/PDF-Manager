# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""How well a PDF is protected, and making it better.

``strength`` says how hard a password is to guess, while it's being typed.
It's an estimate made on this computer from the password's length and
shape (nothing is sent anywhere): a guide, not a promise.

``info`` names the encryption a file has, without needing its password.
PDFs protected years ago, or by old programs, use RC4, which no longer
protects: the 40-bit kind is broken in minutes on an ordinary computer.
``upgrade`` writes the same file with AES-256, with the same password and
the same limits on printing, copying and changes.
"""

import io
import re
import secrets

from pdfmanager import core

LABELS = ("Very weak", "Weak", "Fair", "Good", "Strong")
# The passwords tried first by anyone guessing (the head of every leaked
# list), and the words such lists are built on.
COMMON = frozenset("""
    password passw0rd password1 password123 123456 1234567 12345678 123456789 1234567890 12345 1234 111111
    000000 123123 654321 666666 121212 112233 qwerty qwerty123 qwertyuiop asdfgh asdfghjkl zxcvbnm 1q2w3e4r
    1qaz2wsx qazwsx abc123 abcd1234 iloveyou letmein welcome welcome1 admin administrator root login master
    monkey dragon sunshine princess football baseball superman batman shadow michael jennifer hunter
    trustno1 secret changeme default guest test test123 hello hello123 freedom whatever starwars pdf adobe
    document confidential private office company summer winter spring autumn
""".split())
ROWS = ("qwertyuiop", "asdfghjkl", "zxcvbnm", "1234567890", "abcdefghijklmnopqrstuvwxyz", "qwertzuiop", "azertyuiop")
LEET = str.maketrans("0134578@$!", "oieastbasi")
GUESSES = (1e6, 1e8, 1e10, 1e12)  # the fewest guesses for Weak, Fair, Good, Strong


def _runs(lowered):
    """How much of the password is a run along a keyboard row or the
    alphabet, or one character repeated (in characters)."""
    covered = 0
    i = 0
    while i < len(lowered):
        best = 1
        for row in ROWS:
            for line in (row, row[::-1]):
                at = line.find(lowered[i])
                n = 1
                while at >= 0 and i + n < len(lowered) and at + n < len(line) and line[at + n] == lowered[i + n]:
                    n += 1
                best = max(best, n)
        n = 1
        while i + n < len(lowered) and lowered[i + n] == lowered[i]:
            n += 1
        best = max(best, n)
        if best >= 3:
            covered += best
        i += best if best >= 3 else 1
    return covered


def strength(password):
    """{"score": 0 (very weak) to 4 (strong), "label", "advice"} for a
    password. An empty one has no label."""
    password = str(password or "")[:200]  # (longer than that makes no difference to the answer)
    if not password:
        return {"score": 0, "label": "", "advice": ""}
    lowered = password.lower()
    plain = lowered.translate(LEET)
    letters = re.sub(r"[^a-z]", "", plain)
    advice = ""
    pool = ((26 if re.search(r"[a-z]", password) else 0) + (26 if re.search(r"[A-Z]", password) else 0)
            + (10 if re.search(r"\d", password) else 0) + (33 if re.search(r"[^\w\s]|_", password) else 0)
            + (1 if " " in password else 0) + (40 if re.search(r"[^\x00-\x7f]", password) else 0)) or 26
    guesses = float(pool) ** len(password)
    words = len([w for w in re.split(r"[\s\-_.,]+", password) if len(w) >= 3])
    if words >= 3:
        # A phrase: each word is one choice among a few thousand, not letter by letter.
        guesses = min(guesses, 7776.0 ** words)

    bare = lowered
    while bare and not bare[-1].isalpha():  # (the tail of digits and signs, taken off one by one)
        bare = bare[:-1]
    stem, tail = bare.translate(LEET), lowered[len(bare):]  # "Passw0rd1!" is "password" and "1!"
    if lowered in COMMON or plain in COMMON or stem in COMMON or letters in COMMON:
        guesses, advice = min(guesses, 1e3), "This is one of the most common passwords: it's guessed at once."
    elif password.isdigit():
        guesses = min(guesses, 10.0 ** len(password) / (50 if len(password) in (6, 8) else 1))
        advice = "Only numbers (a date? a phone number?) are quickly tried. Add words."
    else:
        runs = _runs(lowered)
        if runs:
            guesses = max(1e2, guesses / float(pool) ** (runs - 1))
            if runs >= len(password) - 1:
                advice = "Keys in a row and repeated characters are tried early."
        if len(set(lowered)) <= 2:
            guesses = min(guesses, 1e4)
        if stem and len(stem) >= 4 and stem.isalpha() and len(password) - len(stem) <= 4 and words < 3:
            # One word with a few digits or signs after it: a dictionary word plus a tail.
            ending = 200.0 if re.fullmatch(r"(19|20)\d\d", tail) else 10.0 ** len(tail)  # (a year is a common ending)
            guesses = min(guesses, 2e5 * ending * (2 if password != lowered else 1)
                          * (20 if stem != bare else 1))  # (capitals, and 0 for o: a little harder)
            advice = advice or "A single word with numbers after it is easy to guess. Use several words."
    score = sum(guesses >= limit for limit in GUESSES)
    if not advice:
        advice = ("" if score == 4 else "Longer is stronger: three or four unrelated words work well."
                  if len(password) < 14 else "Mix in another word or a few signs.")
    if score == 4:
        advice = "Hard to guess. Keep it somewhere safe: a lost password can't be recovered."
    return {"score": score, "label": LABELS[score], "advice": advice}


def info(path):
    """{"protected", "method" ("RC4, 40-bit", "AES, 256-bit"...), "weak",
    "advice"} for the PDF at ``path``. The password isn't needed: how a file
    is encrypted is written in the open."""
    nothing = {"protected": False, "method": "", "weak": False, "advice": ""}
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        encrypt = reader.trailer.get("/Encrypt")
        encrypt = encrypt.get_object() if encrypt is not None else None
    except Exception:
        return nothing
    if encrypt is None:
        return nothing
    try:
        version = int(encrypt.get("/V", 0))
        length = int(encrypt.get("/Length", 40))
    except (TypeError, ValueError):
        version, length = 0, 40
    method, weak = "unknown", False
    if version in (0, 1):
        method, weak = "RC4, 40-bit", True
    elif version in (2, 3):
        method, weak = f"RC4, {length}-bit", True
    elif version == 4:
        filters = encrypt.get("/CF")
        filters = filters.get_object() if filters is not None else {}
        name = encrypt.get("/StmF", "/StdCF")
        chosen = filters.get(name) if hasattr(filters, "get") else None
        chosen = chosen.get_object() if chosen is not None else {}
        kind = chosen.get("/CFM") if hasattr(chosen, "get") else None
        method, weak = ("AES, 128-bit", False) if kind == "/AESV2" else ("RC4, 128-bit", True)
    elif version >= 5:
        method = "AES, 256-bit"
    advice = ""
    if weak:
        advice = ("This old protection is broken in minutes on an ordinary computer, whatever the password."
                  if method == "RC4, 40-bit" else
                  "This older protection (RC4) is no longer considered safe. AES-256 is today's standard.")
    return {"protected": True, "method": method, "weak": weak, "advice": advice}


_PAD = bytes.fromhex("28bf4e5e4e758a4164004e56fffa01082e2e00b6d0683e802f0ca9fe6453697a")


def _user_password(path, owner_password):
    """The password to open an RC4-protected PDF, worked out from its
    permissions password (the old scheme keeps it, scrambled with that
    password: PDF 1.7, algorithm 7). "" when anyone can open the file.
    pypdf's own reading of the old scheme does the work."""
    from pypdf import PdfReader
    from pypdf._crypt_providers import rc4_decrypt
    from pypdf._encryption import AlgV4

    encrypt = PdfReader(str(path)).trailer["/Encrypt"].get_object()
    revision = int(encrypt.get("/R", 2))
    bits = int(encrypt.get("/Length", 40)) if revision >= 3 else 40
    scrambled = encrypt["/O"]
    scrambled = scrambled.original_bytes if hasattr(scrambled, "original_bytes") else bytes(scrambled)
    key = AlgV4.compute_O_value_key(owner_password.encode("latin-1", "replace"), revision, bits)
    if revision == 2:
        plain = rc4_decrypt(key, scrambled)
    else:
        plain = scrambled
        for i in range(19, -1, -1):
            plain = rc4_decrypt(bytes(b ^ i for b in key), plain)
    end = -1
    for cut in range(33):  # the password, then as much of the padding as fits in 32 bytes
        if plain[cut:32] == _PAD[:32 - cut]:
            end = cut
            break
    return plain[:end if end >= 0 else 32].decode("latin-1")


def upgrade(path, output, password):
    """Write the PDF with AES-256 instead of its old encryption, with the
    same password and the same limits. Returns what was done, in words.

    With the permissions password, the password to open and the limits are
    all kept as they were. With only the password to open, the limits stay
    but their old password can't be carried over (it isn't known): a new
    random one guards them, as unknown to everyone as the old one was to
    whoever is upgrading."""
    import pikepdf

    before = info(path)
    if not before["protected"]:
        raise core.PdfManagerError(f"{path} isn't protected: use Add password to protect it")
    if not before["weak"]:
        raise core.PdfManagerError(f"{path} is already protected with {before['method']}")
    try:
        pdf = pikepdf.open(path, password=password or "")
    except pikepdf.PasswordError:
        raise core.PdfManagerError(f"wrong password for {path}" if password else
                                   f"{path} needs its password to be protected again") from None
    except pikepdf.PdfError as exc:
        raise core.PdfManagerError(f"{path} can't be read ({exc})") from None
    with pdf:
        allow = pdf.allow
        limited = not all(getattr(allow, name) for name in (
            "accessibility", "extract", "modify_annotation", "modify_assembly", "modify_form",
            "modify_other", "print_lowres", "print_highres"))
        typed = password or ""
        if pdf.owner_password_matched:
            # Everything is known: qpdf recovers the password to open from the old encryption.
            user = _user_password(path, typed)
            owner, kept = typed, "its passwords and limits are as they were"
            if not owner or owner == user:
                owner = user if not limited else secrets.token_urlsafe(24)
        else:
            user = typed
            if limited:
                owner, kept = secrets.token_urlsafe(24), (
                    "the password to open it is the same, and the limits on printing, copying or changes "
                    "stay; their old password no longer lifts them (upgrade with the permissions password "
                    "to keep it)")
            else:
                owner, kept = typed, "its password is the same"
        buffer = io.BytesIO()
        pdf.save(buffer, encryption=pikepdf.Encryption(user=user, owner=owner, aes=True, R=6, allow=allow))
    core._write(core._Bytes(buffer.getvalue()), output)
    return f"was {before['method']}, now AES-256; {kept}"
