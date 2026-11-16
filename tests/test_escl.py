# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""0.44.0 (escl.py): scanning from a network scanner without a driver
(eSCL / AirScan), against a fake scanner: a small web server that answers
the way the protocol says."""

import io
import struct
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from PIL import Image

from pdfmanager import appdata, escl, progress

CAPS = """<?xml version="1.0" encoding="UTF-8"?>
<scan:ScannerCapabilities xmlns:scan="http://schemas.hp.com/imaging/escl/2011/05/03" xmlns:pwg="http://www.pwg.org/schemas/2010/12/sm">
  <pwg:Version>2.63</pwg:Version><pwg:MakeAndModel>Acme OfficeScan 300</pwg:MakeAndModel>
  <scan:Platen><scan:PlatenInputCaps>
    <scan:MaxWidth>2550</scan:MaxWidth><scan:MaxHeight>3508</scan:MaxHeight>
    <scan:SettingProfiles><scan:SettingProfile>
      <scan:ColorModes><scan:ColorMode>RGB24</scan:ColorMode><scan:ColorMode>Grayscale8</scan:ColorMode></scan:ColorModes>
      <scan:DocumentFormats><pwg:DocumentFormat>image/jpeg</pwg:DocumentFormat><pwg:DocumentFormat>application/pdf</pwg:DocumentFormat></scan:DocumentFormats>
      <scan:SupportedResolutions><scan:DiscreteResolutions>
        <scan:DiscreteResolution><scan:XResolution>100</scan:XResolution><scan:YResolution>100</scan:YResolution></scan:DiscreteResolution>
        <scan:DiscreteResolution><scan:XResolution>300</scan:XResolution><scan:YResolution>300</scan:YResolution></scan:DiscreteResolution>
        <scan:DiscreteResolution><scan:XResolution>600</scan:XResolution><scan:YResolution>600</scan:YResolution></scan:DiscreteResolution>
      </scan:DiscreteResolutions></scan:SupportedResolutions>
    </scan:SettingProfile></scan:SettingProfiles>
  </scan:PlatenInputCaps></scan:Platen>
  %s
</scan:ScannerCapabilities>"""
ADF = """<scan:Adf><scan:AdfSimplexInputCaps>
    <scan:MaxWidth>2550</scan:MaxWidth><scan:MaxHeight>4200</scan:MaxHeight>
    <scan:SettingProfiles><scan:SettingProfile>
      <scan:ColorModes><scan:ColorMode>RGB24</scan:ColorMode><scan:ColorMode>Grayscale8</scan:ColorMode></scan:ColorModes>
      <scan:SupportedResolutions><scan:DiscreteResolutions>
        <scan:DiscreteResolution><scan:XResolution>300</scan:XResolution><scan:YResolution>300</scan:YResolution></scan:DiscreteResolution>
      </scan:DiscreteResolutions></scan:SupportedResolutions>
    </scan:SettingProfile></scan:SettingProfiles>
  </scan:AdfSimplexInputCaps><scan:AdfDuplexInputCaps><scan:MaxWidth>2550</scan:MaxWidth><scan:MaxHeight>4200</scan:MaxHeight>
  </scan:AdfDuplexInputCaps></scan:Adf>"""
STATUS = """<?xml version="1.0" encoding="UTF-8"?>
<scan:ScannerStatus xmlns:scan="http://schemas.hp.com/imaging/escl/2011/05/03" xmlns:pwg="http://www.pwg.org/schemas/2010/12/sm">
<pwg:Version>2.63</pwg:Version><pwg:State>%s</pwg:State><scan:AdfState>%s</scan:AdfState></scan:ScannerStatus>"""


def jpeg(text_shade=0):
    out = io.BytesIO()
    Image.new("L", (60, 80), 255 - text_shade).save(out, "JPEG")
    return out.getvalue()


class Fake:
    """What the fake scanner holds and saw."""

    def __init__(self, feeder=True, sheets=3):
        self.feeder, self.sheets = feeder, sheets
        self.adf, self.state = "ScannerAdfLoaded", "Idle"
        self.busy_first = 0          # NextDocument answers 503 this many times first
        self.jam_after = None        # the feeder jams after this many pages
        self.refuse_job = None       # a status for POST ScanJobs instead of 201
        self.jobs, self.deleted, self.settings, self.sent = 0, [], [], 0


@pytest.fixture
def scanner(tmp_path, monkeypatch):
    monkeypatch.setattr(appdata, "_data_dir", lambda: tmp_path / "zpdf-data")
    fake = Fake()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def answer(self, status, body=b"", kind="text/xml", headers=()):
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            for k, v in headers:
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/eSCL/ScannerCapabilities":
                return self.answer(200, (CAPS % (ADF if fake.feeder else "")).encode())
            if self.path == "/eSCL/ScannerStatus":
                return self.answer(200, (STATUS % (fake.state, fake.adf)).encode())
            if self.path.endswith("/NextDocument") and self.path.startswith("/eSCL/ScanJobs/"):
                if fake.busy_first:
                    fake.busy_first -= 1
                    return self.answer(503)
                source = "Feeder" if b"Feeder" in fake.settings[-1] else "Platen"
                limit = fake.sheets * (2 if b"<scan:Duplex>true" in fake.settings[-1] else 1) if source == "Feeder" else 1
                if fake.jam_after is not None and fake.sent >= fake.jam_after:
                    fake.adf, fake.state = "ScannerAdfJam", "Stopped"
                    return self.answer(500)
                if fake.sent >= limit:
                    fake.adf = "ScannerAdfEmpty"
                    return self.answer(404)
                fake.sent += 1
                return self.answer(200, jpeg(fake.sent * 20), "image/jpeg")
            return self.answer(404)

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            if self.path != "/eSCL/ScanJobs":
                return self.answer(404)
            fake.settings.append(body)
            if fake.refuse_job:
                return self.answer(fake.refuse_job)
            fake.jobs, fake.sent = fake.jobs + 1, 0
            host = self.headers.get("Host")
            return self.answer(201, headers=[("Location", f"http://{host}/eSCL/ScanJobs/{fake.jobs}")])

        def do_DELETE(self):
            fake.deleted.append(self.path)
            return self.answer(200)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    fake.url = f"http://127.0.0.1:{srv.server_address[1]}/eSCL"
    fake.id = "escl:" + fake.url
    fake.port = srv.server_address[1]
    yield fake
    srv.shutdown()
    srv.server_close()


def test_what_a_scanner_can_do(scanner):
    caps = escl.capabilities(scanner.id)
    assert caps["name"] == "Acme OfficeScan 300" and caps["flatbed"] and caps["feeder"] and caps["duplex"]
    assert caps["modes"] == ["colour", "grey"] and caps["resolutions"] == [100, 300, 600]
    assert caps["max"]["flatbed"] == (2550, 3508) and "image/jpeg" in caps["formats"]
    scanner.feeder = False
    caps = escl.capabilities(scanner.id)
    assert caps["flatbed"] and not caps["feeder"] and not caps["duplex"]


def test_one_page_from_the_glass(scanner):
    seen = []
    pages = escl.scan(scanner.id, mode="grey", dpi=300, source="flatbed", on_page=lambda image, dpi: seen.append(dpi))
    assert len(pages) == 1 and seen == [300] and pages[0][0].size == (60, 80) and pages[0][0].info["dpi"] == (300, 300)
    sent = scanner.settings[-1].decode()
    assert "<pwg:InputSource>Platen</pwg:InputSource>" in sent and "<scan:ColorMode>Grayscale8</scan:ColorMode>" in sent
    assert "<scan:XResolution>300</scan:XResolution>" in sent and "<pwg:Width>2550</pwg:Width>" in sent
    assert "Duplex" not in sent and scanner.deleted == ["/eSCL/ScanJobs/1"]      # the job is ended


def test_every_page_in_the_feeder_and_both_sides(scanner):
    scanner.busy_first = 2                       # "not ready yet" twice, then the pages
    pages = escl.scan(scanner.id, mode="colour", dpi=300, source="feeder", pause=0.01)
    assert len(pages) == 3 and "<pwg:InputSource>Feeder</pwg:InputSource>" in scanner.settings[-1].decode()
    assert "<scan:Duplex>false</scan:Duplex>" in scanner.settings[-1].decode()
    scanner.adf = "ScannerAdfLoaded"
    pages = escl.scan(scanner.id, source="feeder", duplex=True, pause=0.01)
    assert len(pages) == 6 and "<scan:Duplex>true</scan:Duplex>" in scanner.settings[-1].decode()
    assert "<pwg:Height>4200</pwg:Height>" in scanner.settings[-1].decode()


def test_the_nearest_setting_the_scanner_offers_is_used(scanner):
    escl.scan(scanner.id, mode="bw", dpi=200, source="flatbed")          # no black-and-white, no 200 dpi
    sent = scanner.settings[-1].decode()
    assert "<scan:ColorMode>Grayscale8</scan:ColorMode>" in sent and "<scan:XResolution>300</scan:XResolution>" in sent


def test_what_goes_wrong_is_said_in_plain_words(scanner):
    scanner.sheets, scanner.adf = 0, "ScannerAdfEmpty"
    with pytest.raises(escl.EsclError, match="feeder is empty") as empty:
        escl.scan(scanner.id, source="feeder", pause=0.01)
    assert empty.value.code == "empty" and scanner.deleted                 # the job is still ended
    scanner.sheets, scanner.adf, scanner.jam_after = 3, "ScannerAdfLoaded", 1
    with pytest.raises(escl.EsclError, match="jammed") as jam:
        escl.scan(scanner.id, source="feeder", pause=0.01)
    assert jam.value.code == "jam"
    scanner.jam_after, scanner.adf, scanner.state, scanner.refuse_job = None, "ScannerAdfLoaded", "Idle", 503
    with pytest.raises(escl.EsclError, match="busy"):
        escl.scan(scanner.id)
    scanner.refuse_job, scanner.feeder = None, False
    with pytest.raises(escl.EsclError, match="no document feeder"):
        escl.scan(scanner.id, source="feeder")
    with pytest.raises(escl.EsclError, match="didn't answer") as gone:
        escl.capabilities("escl:http://127.0.0.1:9/eSCL")
    assert gone.value.code == "offline"
    with pytest.raises(escl.EsclError, match="isn't a network scanner"):
        escl.scan("{6BDD1FC6-WIA}")
    with pytest.raises(escl.EsclError):
        escl.capabilities("escl:file:///etc/passwd")


def test_a_cancelled_scan_ends_the_scanners_job(scanner):
    scanner.sheets = 50

    def stop(image, dpi):
        raise progress.Cancelled()

    with pytest.raises(progress.Cancelled):
        escl.scan(scanner.id, source="feeder", on_page=stop, pause=0.01)
    assert scanner.deleted == ["/eSCL/ScanJobs/1"]


def test_a_scanner_added_by_its_address_is_remembered(scanner):
    device = escl.add(f"127.0.0.1:{scanner.port}")
    assert device["id"] == scanner.id and device["name"] == "Acme OfficeScan 300 (network)"
    assert device["feeder"] and device["duplex"] and device["found"] == "added"
    assert [d["id"] for d in escl.devices(look=False)] == [scanner.id]
    escl.add(scanner.url)                                                   # again, with its whole address: once
    assert len(escl.devices(look=False)) == 1
    for bad in ("", "not an address!", "http://"):
        with pytest.raises(escl.EsclError):
            escl.add(bad)
    with pytest.raises(escl.EsclError, match="didn't answer"):
        escl.add("127.0.0.1:9")
    escl.forget(scanner.id)
    assert escl.devices(look=False) == []


def packet(records):
    """An mDNS answer with the given records: (name, type, rdata bytes)."""
    def name(text):
        return b"".join(bytes([len(p)]) + p.encode() for p in text.split(".")) + b"\x00"

    out = struct.pack("!6H", 0, 0x8400, 0, len(records), 0, 0)
    for owner, kind, data in records:
        out += name(owner) + struct.pack("!2HIH", kind, 1, 120, len(data)) + data
    return out, name


def test_scanners_that_answer_on_the_network_are_understood():
    _, name = packet([])
    txt = b"".join(bytes([len(t)]) + t for t in (b"ty=Brother MFC-L2710", b"rs=eSCL", b"is=platen,adf", b"duplex=T"))
    data, _ = packet([
        ("_uscan._tcp.local", 12, name("Brother MFC._uscan._tcp.local")),
        ("Brother MFC._uscan._tcp.local", 33, struct.pack("!3H", 0, 0, 80) + name("brother.local")),
        ("Brother MFC._uscan._tcp.local", 16, txt),
        ("brother.local", 1, bytes([192, 168, 1, 40])),
        ("_uscans._tcp.local", 12, name("Brother MFC._uscans._tcp.local")),
        ("Brother MFC._uscans._tcp.local", 33, struct.pack("!3H", 0, 0, 443) + name("brother.local")),
        ("_uscans._tcp.local", 12, name("Secure Only._uscans._tcp.local")),
        ("Secure Only._uscans._tcp.local", 33, struct.pack("!3H", 0, 0, 8443) + name("secure.local")),
        ("secure.local", 1, bytes([192, 168, 1, 41])),
    ])
    found = escl.devices_from(escl.parse_answers(data))
    by = {d["url"]: d for d in found}
    assert set(by) == {"http://192.168.1.40:80/eSCL", "https://192.168.1.41:8443/eSCL"}   # plain kept when both are offered
    brother = by["http://192.168.1.40:80/eSCL"]
    assert brother["name"] == "Brother MFC-L2710 (network)" and brother["feeder"] and brother["flatbed"] and brother["duplex"]
    assert by["https://192.168.1.41:8443/eSCL"]["name"] == "Secure Only (network)"
    # damaged packets are refused, not followed
    for bad in (b"", data[:20], data[:-3], struct.pack("!6H", 0, 0, 0, 1, 0, 0) + b"\xc0\x0c" + b"\x00" * 10):
        with pytest.raises((ValueError, struct.error, IndexError)):
            escl.parse_answers(bad)
    question = escl._question(escl.SERVICES)
    assert b"\x06_uscan\x04_tcp\x05local\x00" in question and question[4:6] == b"\x00\x02"
