# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Scanning from a network scanner without a driver: eSCL, the protocol
Apple calls AirScan and Mopria calls Mopria Scan. Most network scanners
and multifunction printers of the last decade speak it.

    escl.discover()                 # scanners that answer on this network
    escl.add("192.168.1.40")        # one by its address, remembered
    escl.devices()                  # found + remembered: [{"id", "name", "feeder", "flatbed", "duplex", ...}]
    escl.scan(device_id, mode="grey", dpi=300, source="feeder", duplex=False, on_page=...)

How it works (all on the local network; nothing goes anywhere else):

- **Finding:** one multicast DNS question ("who offers ``_uscan._tcp`` or
  ``_uscans._tcp``?") to 224.0.0.251:5353. Each scanner answers with its
  name, address, port and the path its eSCL service lives under.
- **What it can do:** ``GET <root>/ScannerCapabilities``, an XML document:
  flatbed and/or feeder, both sides, colour modes, resolutions, the
  largest page.
- **Scanning:** ``POST <root>/ScanJobs`` with the settings as XML; the
  answer's ``Location`` is the job. ``GET <job>/NextDocument`` gives one
  page (a JPEG) each time, until 404 says there are no more. ``DELETE
  <job>`` ends it. 503 means "not ready yet": asked again shortly.

A device id is ``escl:http://host:port/root``. Scanners that only offer
HTTPS use a certificate they made themselves; it is accepted for the
device asked for, which is on the local network.
"""

import http.client
import io
import re
import socket
import ssl
import struct
import threading
import time
import urllib.parse
from xml.etree import ElementTree

from pdfmanager import core, progress

SCAN_NS = "http://schemas.hp.com/imaging/escl/2011/05/03"
PWG_NS = "http://www.pwg.org/schemas/2010/12/sm"
NS = {"scan": SCAN_NS, "pwg": PWG_NS}
SERVICES = ("_uscan._tcp.local", "_uscans._tcp.local")
MDNS = ("224.0.0.251", 5353)
COLOUR = {"colour": "RGB24", "grey": "Grayscale8", "bw": "BlackAndWhite1"}
TIMEOUT = 20            # seconds for a request to a scanner
PAGE_TIMEOUT = 120      # for a page to arrive (the scanner is moving paper)
MAX_XML = 2 * 1024 * 1024
MAX_PAGE = 300 * 1024 * 1024
MAX_PAGES = 500
BUSY_TRIES = 40         # 503s in a row before giving up (about a minute)

_lock = threading.Lock()
_found = {}             # id -> device, from the last discover()


class EsclError(core.PdfManagerError):
    """Scanning over the network failed; the message says why in plain
    words. ``code``: "offline", "busy", "empty", "jam", "open", "refused"…"""

    def __init__(self, message, code="general"):
        super().__init__(message)
        self.code = code


# ---- finding scanners: multicast DNS --------------------------------------------------

def _name(data, at):
    """A DNS name at ``at`` (following compression pointers): (name, next offset)."""
    labels, jumped, after, hops = [], False, at, 0
    while True:
        if at >= len(data):
            raise ValueError("a name runs off the packet")
        size = data[at]
        if size == 0:
            at += 1
            break
        if size & 0xC0 == 0xC0:
            if at + 1 >= len(data):
                raise ValueError("a pointer runs off the packet")
            if not jumped:
                after = at + 2
            at, jumped, hops = ((size & 0x3F) << 8) | data[at + 1], True, hops + 1
            if hops > 40:
                raise ValueError("a name points in circles")
            continue
        at += 1
        labels.append(data[at:at + size].decode("utf-8", "replace"))
        at += size
    return ".".join(labels), (after if jumped else at)


def _question(names):
    """One DNS query asking for the PTR records of ``names``, answers wanted
    sent straight back to us (the QU bit)."""
    out = struct.pack("!6H", 0, 0, len(names), 0, 0, 0)
    for name in names:
        for label in name.split("."):
            raw = label.encode()
            out += bytes([len(raw)]) + raw
        out += b"\x00" + struct.pack("!2H", 12, 0x8001)
    return out


def parse_answers(data):
    """The records of one mDNS packet that matter here: {"ptr": {service:
    [instance]}, "srv": {instance: (host, port)}, "txt": {instance: {key:
    value}}, "a": {host: address}}. Raises ValueError on a damaged packet."""
    found = {"ptr": {}, "srv": {}, "txt": {}, "a": {}}
    if len(data) < 12:
        raise ValueError("too short")
    _, _, questions, answers, authority, extra = struct.unpack("!6H", data[:12])
    at = 12
    for _ in range(questions):
        _, at = _name(data, at)
        at += 4
    for _ in range(answers + authority + extra):
        name, at = _name(data, at)
        if at + 10 > len(data):
            raise ValueError("a record runs off the packet")
        kind, _, _, size = struct.unpack("!2HIH", data[at:at + 10])
        at += 10
        end = at + size
        if end > len(data):
            raise ValueError("a record runs off the packet")
        name = name.lower()
        if kind == 12:    # PTR: a service has this instance
            target, _ = _name(data, at)
            found["ptr"].setdefault(name, []).append(target)
        elif kind == 33 and size >= 7:  # SRV: where an instance is
            port = struct.unpack("!H", data[at + 4:at + 6])[0]
            host, _ = _name(data, at + 6)
            found["srv"][name] = (host.lower(), port)
        elif kind == 16:  # TXT: key=value strings
            pairs, p = {}, at
            while p < end:
                n = data[p]
                key, _, value = data[p + 1:p + 1 + n].decode("utf-8", "replace").partition("=")
                if key:
                    pairs[key.lower()] = value
                p += 1 + n
            found["txt"][name] = pairs
        elif kind == 1 and size == 4:   # A: a host's address
            found["a"][name] = socket.inet_ntoa(data[at:end])
        at = end
    return found


def devices_from(found):
    """Scanners among parse_answers() results (merged over packets)."""
    out = []
    for service in SERVICES:
        secure = service.startswith("_uscans")
        for instance in found["ptr"].get(service, []):
            key = instance.lower()
            srv, txt = found["srv"].get(key), found["txt"].get(key, {})
            if not srv:
                continue
            host, port = srv
            address = found["a"].get(host) or host.rstrip(".")
            root = "/" + (txt.get("rs") or "eSCL").strip("/")
            name = txt.get("ty") or instance.split("._")[0]
            sources = (txt.get("is") or "platen").lower()
            out.append(_device(f"{'https' if secure else 'http'}://{address}:{port}{root}", name,
                               flatbed="platen" in sources or "adf" not in sources, feeder="adf" in sources,
                               duplex=(txt.get("duplex") or "").upper() == "T", found="network"))
    # A scanner offering both keeps the plain one (no certificate to wave through).
    plain = {urllib.parse.urlsplit(d["url"]).hostname for d in out if d["url"].startswith("http://")}
    return [d for d in out if d["url"].startswith("http://") or urllib.parse.urlsplit(d["url"]).hostname not in plain]


def _device(url, name, flatbed=True, feeder=False, duplex=False, found="network"):
    return {"id": "escl:" + url, "url": url, "name": f"{name} (network)", "flatbed": flatbed, "feeder": feeder,
            "duplex": duplex, "network": True, "found": found}


def _local_ipv4s():
    """Local IPv4 addresses to send mDNS multicast out of (so virtual
    adapters on Windows don't swallow the only query)."""
    addrs = []
    for target in (("10.255.255.255", 1), ("192.168.1.1", 1)):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
                probe.connect(target)
                ip = probe.getsockname()[0]
                if ip and not ip.startswith("127.") and ip not in addrs:
                    addrs.append(ip)
        except OSError:
            pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip and not ip.startswith("127.") and ip not in addrs:
                addrs.append(ip)
    except OSError:
        pass
    return addrs


def _send_query(sock, packet, interfaces):
    try:
        sock.sendto(packet, MDNS)
    except OSError:
        pass
    for ip in interfaces:
        try:
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(ip))
            sock.sendto(packet, MDNS)
        except OSError:
            continue


def discover(wait=2.5):
    """Ask the local network which scanners offer eSCL and listen for
    ``wait`` seconds. Returns their devices ([] when none answer, or when
    the network doesn't let the question out)."""
    merged = {"ptr": {}, "srv": {}, "txt": {}, "a": {}}
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    except OSError:
        return []
    try:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)   # (this network only)
        sock.settimeout(0.3)
        packet = _question(SERVICES)
        interfaces = _local_ipv4s()
        _send_query(sock, packet, interfaces)
        started = time.monotonic()
        until = started + wait
        resent = wait < 0.8
        while time.monotonic() < until:
            if not resent and not merged["ptr"] and time.monotonic() - started >= 0.5:
                _send_query(sock, packet, interfaces)
                resent = True
            try:
                data, _ = sock.recvfrom(9000)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                got = parse_answers(data)
            except (ValueError, struct.error, IndexError):
                continue  # (not ours to understand)
            for kind in ("srv", "txt", "a"):
                merged[kind].update(got[kind])
            for service, instances in got["ptr"].items():
                merged["ptr"].setdefault(service, [])
                merged["ptr"][service] += [i for i in instances if i not in merged["ptr"][service]]
    except OSError:
        return []
    finally:
        sock.close()
    found = devices_from(merged)
    with _lock:
        _found.clear()
        _found.update({d["id"]: d for d in found})
    return found


# ---- talking to one scanner -----------------------------------------------------------

def _url(device_id):
    if not str(device_id).startswith("escl:"):
        raise EsclError("that isn't a network scanner", "refused")
    url = str(device_id)[5:]
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise EsclError("that network scanner's address isn't one ZPDF understands", "refused")
    return parts


def _request(parts, method, path, body=None, timeout=TIMEOUT, limit=MAX_XML):
    """One request to a scanner: (status, headers, bytes). ``path`` is
    relative to the device's root, or absolute (a job's own address)."""
    if path.startswith(("http://", "https://")):
        # A job's address as the scanner gave it. Scanners name themselves in
        # many ways there (their host name, another address of theirs), so
        # only its path is used: the request still goes to the scanner we're
        # talking to, never to wherever the answer points.
        target = urllib.parse.urlsplit(path).path or "/"
    elif path.startswith("/"):
        target = path
    else:
        target = parts.path.rstrip("/") + "/" + path
    port = parts.port or (443 if parts.scheme == "https" else 80)
    if parts.scheme == "https":
        context = ssl.create_default_context()
        context.check_hostname = False          # (the scanner's own certificate, on the local network)
        context.verify_mode = ssl.CERT_NONE
        conn = http.client.HTTPSConnection(parts.hostname, port, timeout=timeout, context=context)
    else:
        conn = http.client.HTTPConnection(parts.hostname, port, timeout=timeout)
    try:
        headers = {"Content-Type": "text/xml"} if body is not None else {}
        conn.request(method, target, body=body, headers=headers)
        res = conn.getresponse()
        data = res.read(limit + 1)
        if len(data) > limit:
            raise EsclError("the scanner sent more than ZPDF takes in one go", "refused")
        return res.status, {k.lower(): v for k, v in res.getheaders()}, data
    except EsclError:
        raise
    except (OSError, http.client.HTTPException) as exc:
        raise EsclError(f"The scanner at {parts.hostname} didn't answer ({type(exc).__name__}). Check that it is "
                        "switched on and on the same network as this computer.", "offline") from None
    finally:
        conn.close()


class _Refused(Exception):
    """A scanner's XML declares a document type or entities."""


def _xml(data):
    """A scanner's XML answer as an element tree. It is read by expat with
    handlers of our own: one that declares a document type or entities is
    refused at the declaration, before anything could expand (they can be
    made to expand without end), whatever encoding it's written in."""
    import xml.parsers.expat

    def refuse(*_):
        raise _Refused()

    builder = ElementTree.TreeBuilder()
    name = lambda raw: "{" + raw if "}" in raw else raw  # noqa: E731 - "{namespace}tag", as ElementTree names them
    reader = xml.parsers.expat.ParserCreate(namespace_separator="}")
    reader.StartDoctypeDeclHandler = refuse
    reader.EntityDeclHandler = refuse
    reader.StartElementHandler = lambda tag, attrs: builder.start(name(tag), {name(k): v for k, v in attrs.items()})
    reader.EndElementHandler = lambda tag: builder.end(name(tag))
    reader.CharacterDataHandler = builder.data
    try:
        reader.Parse(data, True)
        return builder.close()
    except _Refused:
        raise EsclError("the scanner's answer isn't one ZPDF reads", "refused") from None
    except (xml.parsers.expat.ExpatError, AssertionError):
        raise EsclError("the scanner's answer couldn't be read", "refused") from None


def _ints(root, path):
    out = []
    for node in root.iterfind(path, NS):
        try:
            out.append(int((node.text or "").strip()))
        except ValueError:
            pass
    return out


def capabilities(device_id):
    """What a scanner can do: {"name", "flatbed", "feeder", "duplex",
    "modes": ["colour", "grey", "bw"], "resolutions": [...], "max":
    {source: (width, height) in 1/300 inch}, "formats": [...]}."""
    parts = _url(device_id)
    status, _, data = _request(parts, "GET", "ScannerCapabilities")
    if status != 200:
        raise EsclError(f"The device at {parts.hostname} doesn't offer driverless scanning (it answered {status}). "
                        "Check the address, or look in its settings for AirScan, eSCL or Mopria Scan.", "refused")
    root = _xml(data)
    name = (root.findtext("pwg:MakeAndModel", "", NS) or root.findtext("scan:MakeAndModel", "", NS) or "").strip()
    sources = {"flatbed": root.find("scan:Platen/scan:PlatenInputCaps", NS),
               "feeder": root.find("scan:Adf/scan:AdfSimplexInputCaps", NS),
               "duplex": root.find("scan:Adf/scan:AdfDuplexInputCaps", NS)}
    modes, resolutions, largest, formats = set(), set(), {}, set()
    wire = {v: k for k, v in COLOUR.items()}
    for key, caps in sources.items():
        if caps is None:
            continue
        for mode in caps.iterfind(".//scan:ColorMode", NS):
            if (mode.text or "").strip() in wire:
                modes.add(wire[mode.text.strip()])
        resolutions.update(_ints(caps, ".//scan:DiscreteResolution/scan:XResolution"))
        for span in caps.iterfind(".//scan:XResolutionRange", NS):
            low, high = _ints(span, "scan:Min"), _ints(span, "scan:Max")
            if low and high:
                resolutions.update(r for r in (75, 100, 150, 200, 300, 400, 600) if low[0] <= r <= high[0])
        width, height = _ints(caps, "scan:MaxWidth"), _ints(caps, "scan:MaxHeight")
        if width and height:
            largest[key] = (width[0], height[0])
        formats.update((f.text or "").strip() for f in caps.iterfind(".//pwg:DocumentFormat", NS))
        formats.update((f.text or "").strip() for f in caps.iterfind(".//scan:DocumentFormatExt", NS))
    return {"name": name or parts.hostname, "flatbed": sources["flatbed"] is not None or sources["feeder"] is None,
            "feeder": sources["feeder"] is not None, "duplex": sources["duplex"] is not None,
            "modes": [m for m in ("colour", "grey", "bw") if m in modes] or ["colour", "grey"],
            "resolutions": sorted(resolutions) or [300], "max": largest, "formats": sorted(f for f in formats if f)}


def _state(parts):
    """Why a scanner won't scan, from its status page: an EsclError, or None."""
    try:
        status, _, data = _request(parts, "GET", "ScannerStatus")
        root = _xml(data) if status == 200 else None
    except EsclError:
        return None
    if root is None:
        return None
    adf = (root.findtext("scan:AdfState", "", NS) or "").strip()
    said = {"ScannerAdfEmpty": ("empty", "The document feeder is empty. Put the pages in and scan again."),
            "ScannerAdfJam": ("jam", "Paper is jammed in the feeder. Clear it, put the pages back and scan again."),
            "ScannerAdfDoorOpen": ("open", "The feeder's cover is open. Close it and scan again."),
            "ScannerAdfHatchOpen": ("open", "The feeder's cover is open. Close it and scan again."),
            "ScannerAdfMultipickDetected": ("multifeed", "The feeder took several sheets at once. Put the pages back and scan again."),
            "ScannerAdfMispick": ("jam", "The feeder couldn't take a sheet. Straighten the pages and scan again.")}
    if adf in said:
        return EsclError(said[adf][1], said[adf][0])
    if (root.findtext("pwg:State", "", NS) or "").strip() == "Stopped":
        return EsclError("The scanner has stopped and needs attention: look at its display, then try again.", "attention")
    return None


def _settings(caps, mode, dpi, source, duplex, regions=True):
    feeder = source == "feeder"
    width, height = caps["max"].get("duplex" if feeder and duplex else source) or caps["max"].get("flatbed") or (2550, 3508)
    region_xml = ("<pwg:ScanRegions><pwg:ScanRegion>"
                  "<pwg:ContentRegionUnits>escl:ThreeHundredthsOfInches</pwg:ContentRegionUnits>"
                  f"<pwg:XOffset>0</pwg:XOffset><pwg:YOffset>0</pwg:YOffset><pwg:Width>{width}</pwg:Width><pwg:Height>{height}</pwg:Height>"
                  "</pwg:ScanRegion></pwg:ScanRegions>") if regions else ""
    return ('<?xml version="1.0" encoding="UTF-8"?>'
            f'<scan:ScanSettings xmlns:scan="{SCAN_NS}" xmlns:pwg="{PWG_NS}">'
            "<pwg:Version>2.0</pwg:Version>"
            + region_xml
            + f"<pwg:InputSource>{'Feeder' if feeder else 'Platen'}</pwg:InputSource>"
            f"<scan:ColorMode>{COLOUR[mode]}</scan:ColorMode>"
            f"<scan:XResolution>{dpi}</scan:XResolution><scan:YResolution>{dpi}</scan:YResolution>"
            "<pwg:DocumentFormat>image/jpeg</pwg:DocumentFormat>"
            + (f"<scan:Duplex>{'true' if duplex else 'false'}</scan:Duplex>" if feeder else "")
            + "</scan:ScanSettings>").encode()


def scan(device_id, mode="grey", dpi=300, source="flatbed", duplex=False, on_page=None, pause=1.5):
    """Scan from a network scanner: one page from the glass, or every page
    in the feeder (both sides with ``duplex``). ``on_page(image, dpi)`` is
    called as each page arrives. Returns [(PIL image, dpi)]. Stops between
    pages when the job is cancelled, and ends the scanner's job."""
    from PIL import Image

    parts = _url(device_id)
    if mode not in COLOUR:
        raise EsclError(f"choose a colour setting: {', '.join(COLOUR)}", "refused")
    if source not in ("flatbed", "feeder"):
        raise EsclError("choose Flatbed or Document feeder", "refused")
    dpi = int(dpi)
    progress.say("connecting to the scanner")
    caps = capabilities(device_id)
    if source == "feeder" and not caps["feeder"]:
        raise EsclError("This scanner has no document feeder.", "refused")
    duplex = bool(duplex) and source == "feeder" and caps["duplex"]
    if dpi not in caps["resolutions"]:   # the nearest it offers (the finer of two equally near)
        dpi = min(caps["resolutions"], key=lambda r: (abs(r - dpi), -r))
    if mode not in caps["modes"]:
        mode = "grey" if "grey" in caps["modes"] else caps["modes"][0]
    status, headers, _ = _request(parts, "POST", "ScanJobs", _settings(caps, mode, dpi, source, duplex))
    if status in (400, 409) and _state(parts) is None:
        # Some feeder firmwares reject explicit ScanRegions because the feeder measures paper length itself.
        status, headers, _ = _request(parts, "POST", "ScanJobs", _settings(caps, mode, dpi, source, duplex, regions=False))
    if status == 503:
        raise _state(parts) or EsclError("The scanner is busy. Wait for it to finish, then try again.", "busy")
    if status == 409:
        raise _state(parts) or EsclError("The scanner refused these settings. Try another resolution or colour.", "refused")
    job = headers.get("location")
    if status != 201 or not job:
        raise _state(parts) or EsclError(f"The scanner wouldn't start (it answered {status}).", "refused")
    pages, busy = [], 0
    try:
        while len(pages) < MAX_PAGES:
            progress.check()
            progress.say(f"scanning page {len(pages) + 1}" if source == "feeder" else "scanning")
            try:
                status, headers, data = _request(parts, "GET", job.rstrip("/") + "/NextDocument", timeout=PAGE_TIMEOUT,
                                                 limit=MAX_PAGE)
            except EsclError as exc:
                if pages and exc.code == "offline":
                    why = _state(parts)
                    if why is not None and why.code == "empty":
                        break
                    if why is not None:
                        raise why from None
                    if busy < 3:
                        busy += 1
                        time.sleep(pause)
                        continue
                raise
            if status == 503:               # not ready yet: the paper is still moving
                busy += 1
                if busy > BUSY_TRIES:
                    why = _state(parts)
                    if pages and why is not None and why.code == "empty":
                        break
                    raise why or EsclError("The scanner took too long to send the page.", "busy")
                time.sleep(pause)
                continue
            if status in (204, 404, 410) or (status == 200 and not data):   # no more pages
                if not pages:
                    raise _state(parts) or EsclError(
                        "The document feeder is empty. Put the pages in and scan again." if source == "feeder"
                        else "The scanner sent no page. Try again.", "empty")
                break
            if status != 200:
                why = _state(parts)
                if pages and why is not None and why.code == "empty":
                    break  # the feeder ran out after scanning the pages in the tray
                if pages and why is None and status == 409:
                    if busy < 2:
                        busy += 1
                        time.sleep(pause)
                        continue
                    break
                raise why or EsclError(f"The scanner stopped on page {len(pages) + 1} (it answered {status}).",
                                       "general")
            busy = 0
            try:
                image = Image.open(io.BytesIO(data))
                image.load()
            except Exception:  # noqa: BLE001 - whatever Pillow says of a bad picture
                raise EsclError(f"Page {len(pages) + 1} from the scanner couldn't be read as a picture.", "general") from None
            image.info["dpi"] = (dpi, dpi)
            pages.append((image, dpi))
            if on_page is not None:
                on_page(image, dpi)
            if source != "feeder":
                break
    finally:
        try:
            _request(parts, "DELETE", job, timeout=5)   # (ends the job; one already finished says 404)
        except EsclError:
            pass
    return pages


# ---- the scanners ZPDF knows ----------------------------------------------------------

def _saved():
    from pdfmanager import update

    kept = update.settings().get("network_scanners")
    return [d for d in kept if isinstance(d, dict) and str(d.get("url", "")).startswith(("http://", "https://"))] \
        if isinstance(kept, list) else []


def _keep(items):
    from pdfmanager import update

    update.save_settings(network_scanners=items or None)


def add(address):
    """Remember a scanner by its address ("192.168.1.40", "printer.local",
    or a whole http(s) address with its path), after asking it what it can
    do. Returns its device."""
    text = str(address or "").strip()
    if not text:
        raise EsclError("type the scanner's address, e.g. 192.168.1.40", "refused")
    if not re.match(r"^https?://", text, re.I):
        text = "http://" + text
    parts = urllib.parse.urlsplit(text)
    if not parts.hostname or not re.fullmatch(r"[A-Za-z0-9.\-:\[\]]+", parts.netloc):
        raise EsclError("that doesn't look like an address: try something like 192.168.1.40", "refused")
    tries = [text.rstrip("/")] if parts.path.strip("/") else [f"{parts.scheme}://{parts.netloc}/eSCL"]
    if not parts.path.strip("/") and parts.scheme == "http" and not parts.port:
        tries.append(f"https://{parts.netloc}/eSCL")       # (some answer on HTTPS only)
    last = None
    for url in tries:
        try:
            caps = capabilities("escl:" + url)
        except EsclError as exc:
            last = exc
            continue
        device = _device(url, caps["name"], caps["flatbed"], caps["feeder"], caps["duplex"], found="added")
        kept = [d for d in _saved() if d.get("url") != url]
        kept.append({"url": url, "name": caps["name"], "flatbed": caps["flatbed"], "feeder": caps["feeder"],
                     "duplex": caps["duplex"]})
        _keep(kept[-20:])
        return device
    raise last


def forget(device_id):
    """Take a remembered scanner off the list."""
    url = str(device_id)[5:] if str(device_id).startswith("escl:") else str(device_id)
    _keep([d for d in _saved() if d.get("url") != url])


def devices(look=True, wait=2.5):
    """The network scanners to offer: those answering now (when ``look``)
    and the remembered ones, each once."""
    found = discover(wait) if look else []
    seen = {d["url"] for d in found}
    for kept in _saved():
        if kept["url"] not in seen:
            found.append(_device(kept["url"], kept.get("name") or urllib.parse.urlsplit(kept["url"]).hostname,
                                 bool(kept.get("flatbed", True)), bool(kept.get("feeder")), bool(kept.get("duplex")),
                                 found="added"))
    return found
