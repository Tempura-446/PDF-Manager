# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""A made-up Windows Image Acquisition for the tests: the COM layer
scanner.py talks to (create, get, put, call, hresult, apartment) over
Python objects shaped like WIA's (DeviceManager, DeviceInfos, Device,
Items, Properties, ImageFile), with scanners that have pages to give, an
empty feeder, or are busy or offline.

    wia = FakeCom([FakeScanner("Desk scanner", feeder_pages=[page1, page2])])
    scanner.use(wia)
"""
import contextlib
import threading
import time

from PIL import Image, ImageDraw


class FakeComError(Exception):
    """A failed COM call, with its HRESULT (as the real one has in HResult)."""

    def __init__(self, hresult):
        super().__init__(f"0x{hresult:08X}")
        self.hresult = hresult


def text_page(number=1, size=(1240, 1754), dpi=150, mode="L", blank=False):
    """A scanned page: lines of "words", and the page number as a block of
    marks at the top (so tests can tell pages apart)."""
    page = Image.new("L", size, 250)
    if not blank:
        d = ImageDraw.Draw(page)
        w, h = size
        m = w // 10
        for i in range(number):
            d.rectangle([m + i * w // 25, m, m + i * w // 25 + w // 40, m + w // 25], fill=10)
        y = m + w // 10
        while y < h - m:
            x = m
            while x < w - m:
                d.rectangle([x, y, min(x + w // 12, w - m), y + h // 120], fill=25)
                x += w // 12 + w // 60
            y += h // 40
    page.info["dpi"] = (dpi, dpi)
    return page.convert(mode)


class Prop:
    def __init__(self, value, maximum=None):
        self.Value = value
        self.SubTypeMax = maximum if maximum is not None else value


class Props:
    def __init__(self, values, refuse=()):
        self.values = {str(k): (v if isinstance(v, Prop) else Prop(v)) for k, v in values.items()}
        self.refuse = {str(k) for k in refuse}
        self.log = []

    def Exists(self, pid):
        return str(pid) in self.values

    def Item(self, pid):
        if str(pid) not in self.values:
            raise FakeComError(0x80070057)
        prop = self.values[str(pid)]
        props = self

        class Watched:
            def __getattr__(self, name):
                return getattr(prop, name)

            def __setattr__(self, name, value):
                if str(pid) in props.refuse:
                    raise FakeComError(0x80070057)
                props.log.append((int(pid) if str(pid).isdigit() else pid, value))
                setattr(prop, name, value)

        return Watched()


class ImageFile:
    def __init__(self, image):
        self.image = image

    def SaveFile(self, path):
        self.image.save(path, "PNG", dpi=self.image.info.get("dpi", (150, 150)))


class FakeScanner:
    """One scanner. ``flatbed_pages``: what each press of Scan on the glass
    gives (the last one again when they run out); ``feeder_pages``: the
    pages in the feeder; ``connect_error`` / ``transfer_error``: an HRESULT
    to fail with; ``delay``: seconds each page takes."""

    def __init__(self, name="Fake scanner", device_id=None, flatbed=True, feeder=True, flatbed_pages=None,
                 feeder_pages=None, connect_error=None, transfer_error=None, delay=0.0, png=True,
                 reports_status=True):
        self.name = name
        self.id = device_id or "{6BDD1FC6-810F-11D0-BEC7-08002BE2092F}\\" + name.replace(" ", "")
        self.flatbed, self.feeder = flatbed, feeder
        self.flatbed_pages = list(flatbed_pages or [text_page(1)])
        self.feeder_pages = list(feeder_pages or [])
        self.connect_error, self.transfer_error = connect_error, transfer_error
        self.delay = delay
        self.png = png
        self.reports_status = reports_status
        self.scanned = 0
        self.formats = []
        caps = (1 if feeder else 0) | (2 if flatbed else 0)
        device = {3086: caps, 3088: 2, 3096: 0}
        if reports_status:
            device[3087] = 1 if self.feeder_pages else 0
        self.device_props = Props(device)
        self.item_props = Props({6146: 0, 4103: 3, 4104: 24, 6147: 100, 6148: 100, 6149: 0, 6150: 0,
                                 6151: Prop(850, 2550), 6152: Prop(1100, 3508)})


class _Info:
    def __init__(self, scanner):
        self.s = scanner
        self.DeviceID = scanner.id
        self.Type = 1
        self.Properties = Props({"Name": scanner.name})

    def Connect(self):
        if self.s.connect_error:
            raise FakeComError(self.s.connect_error)
        return _Device(self.s)


class _Device:
    def __init__(self, scanner):
        self.s = scanner
        self.Properties = scanner.device_props
        self.Items = _Items(_Item(scanner))


class _Items:
    def __init__(self, item):
        self.item = item
        self.Count = 1

    def Item(self, i):
        if i != 1:
            raise FakeComError(0x80070057)
        return self.item


class _Item:
    def __init__(self, scanner):
        self.s = scanner
        self.Properties = scanner.item_props

    def Transfer(self, fmt):
        s = self.s
        s.formats.append(fmt)
        if not s.png and fmt.startswith("{B96B3CAF"):
            raise FakeComError(0x8021000C)  # format not supported
        if s.delay:
            time.sleep(s.delay)
        if s.transfer_error:
            raise FakeComError(s.transfer_error)
        feeder = s.device_props.values["3088"].Value == 1
        if feeder:
            if not s.feeder_pages:
                raise FakeComError(0x80210003)  # WIA_ERROR_PAPER_EMPTY
            page = s.feeder_pages.pop(0)
            if "3087" in s.device_props.values:
                s.device_props.values["3087"].Value = 1 if s.feeder_pages else 0
        else:
            page = s.flatbed_pages.pop(0) if len(s.flatbed_pages) > 1 else s.flatbed_pages[0]
        s.scanned += 1
        dpi = s.item_props.values["6147"].Value
        image = page.copy()
        image.info["dpi"] = (dpi, dpi)
        return ImageFile(image)


class _Infos:
    def __init__(self, scanners):
        self.list = [_Info(s) for s in scanners]
        # Another kind of device (a camera) is skipped.
        camera = _Info(FakeScanner("Phone camera"))
        camera.Type = 2
        self.list.insert(0, camera)
        self.Count = len(self.list)

    def Item(self, i):
        return self.list[i - 1]


class _Manager:
    def __init__(self, scanners):
        self.DeviceInfos = _Infos(scanners)


class FakeCom:
    """The COM layer of scanner.py, over the fakes above."""

    def __init__(self, scanners=(), missing=False):
        self.scanners = list(scanners)
        self.missing = missing
        self.threads = set()

    def create(self, progid):
        if self.missing:
            raise FakeComError(0x80040154)  # REGDB_E_CLASSNOTREG
        assert progid == "WIA.DeviceManager", progid
        return _Manager(self.scanners)

    def get(self, obj, name, *index):
        value = getattr(obj, name)
        return value(*index) if index else value

    def put(self, obj, name, value, *index):
        setattr(obj, name, value)

    def call(self, obj, name, *args):
        return getattr(obj, name)(*args)

    @staticmethod
    def hresult(exc):
        return getattr(exc, "hresult", None)

    @contextlib.contextmanager
    def apartment(self):
        self.threads.add(threading.get_ident())
        yield
