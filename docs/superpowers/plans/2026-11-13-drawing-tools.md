# Drawing Tools (CAD-adjacent): Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ZPDF is the free, offline tool for working with drawings as PDFs, the job Bluebeam Revu does for builders and engineers. That means viewing and printing them with their layers, marking them up the way construction teams do, measuring them (already strong), comparing revisions (already there), and passing them to and from CAD programs through DXF.

**Not a goal: full CAD.** No geometry editing with constraints, no blocks, no dimension styles, no 3D, no DWG writing. DWG is closed. The Open Design Alliance libraries are paid, and LibreDWG is GPL, which is incompatible with ZPDF's licence. DWG is only touched through the ODA File Converter, and only if the user installs it themselves.

**Already in ZPDF:**
- true-scale display, the scale read from the PDF (`/VP /Measure`), and calibration;
- snapping to the drawing's lines (`snap.js`);
- lengths, paths, areas with cut-outs, counts and angles, with groups and colours, CSV export, and saving into the PDF (`measuring.js`, `measure.js`);
- overlay compare of two revisions (`overlay.js`);
- stamps, shapes and comments in the reader.

**New dependency (Phases 3 and 4):** `ezdxf` (MIT, pure Python) for reading and writing DXF. Add it to `pyproject.toml`, `requirements.txt` and `notices.py`.

## Global constraints

- Everything is offline, and results open in other programs:
  - DXF output opens in AutoCAD, BricsCAD, DraftSight, LibreCAD and QCAD;
  - PDF output keeps its layers in Acrobat;
  - markups are standard PDF annotations, so Acrobat and Bluebeam see them.
- Scale is never lost: anything measured, exported or imported carries the drawing's real units.
- Big drawings stay usable: a 200,000-segment sheet must open, and export to DXF, within the existing performance limits. Add a `--perf` case.
- Light, dark and forced-colors themes; keyboard reachable; plain words. Python 3.10 to 3.14 on Linux and Windows; no model identifiers in code or commits.

---

### Phase 1 (release A): layers

**Task 1.1: layers panel in the reader**
- [ ] Read the PDF's optional content groups (`/OCProperties`: `/OCGs`, `/D` with `/Order` and `/ON`/`/OFF`, `/Locked`, and `/RBGroups` for radio groups) through pdf.js's `optionalContentConfig`.
- [ ] Show a "Layers" tab in the sidebar, next to Pages, Outline and Comments, only when the file has layers. It shows the nested order with checkboxes; locked layers can't be changed, and a radio group keeps only one layer on.
- [ ] Toggling a layer redraws the pages (pdf.js `optionalContentConfigPromise`). The thumbnails follow.
- [ ] Measuring and snapping only use the visible layers.
- [ ] Test (browser): a two-layer PDF made with reportlab or pikepdf. Hiding the "Dimensions" layer hides its text, and snapping no longer finds its lines.

**Task 1.2: print and save with chosen layers**
- [ ] Print uses the layers currently shown.
- [ ] Add a "Save layer visibility" choice that writes `/D /ON`/`/OFF` to the file. Add a "Flatten layers" tool that bakes the visible layers in and removes the hidden ones' content, using pikepdf and the existing content-stream code, for sending a clean set.
- [ ] Test: after flattening with "Dimensions" hidden, its text is gone from pdfium's text and from the file.

### Phase 2 (release B): construction markups

**Done in 0.38.0** (`markup.py` kinds `polycloud`, `polyline`, `callout`, `dimension`; `annotate.js`; `tests/test_construction_markups.py`, `tests/test_drawing_markups_ui.py`).

**Task 2.1: markup tools on the annotate bar's Shapes**
- [x] **Revision cloud** through clicked corners: `/Polygon` with `/BE << /S /C /I 1 >>`.
- [x] **Callout:** `/FreeText` with `/IT /FreeTextCallout`, `/CL`, `/LE`.
- [x] **Dimension line:** `/Line` with `/LE` arrows, `/Cap true`, `/IT /LineDimension` and a `/Measure` dictionary; ends snap to the drawing; the label is the scaled length in the Scale bar's units.
- [x] **Arrow and polyline** with corners (`/PolyLine`, `/LE`).
- [x] They appear in the Comments tab, delete and undo like other markups, and are saved as standard annotations.
- [ ] Left for later: move, reshape and recolour a placed markup (no markup of the reader's own can be moved yet); arc size choice for clouds.

**Task 2.2: construction stamps**
- [x] Review stamps: Approved as noted; Revise and resubmit; Rejected; For information; Not for construction, with name and date.
- [ ] Left for later: a saved list of the user's own stamps with a colour (one own stamp's words are remembered today).

### Phase 2b (release B2): change a placed markup; a quantity schedule

Asked for after 0.38.0, whose "Not yet" list this is.

**Task 2b.1: move and reshape after placing**
- [ ] `markup.apply` gets `{"op": "change", "page", "id", ...}` for the reader's own markups (clouds, lines and arrows with or without corners, rectangles, ellipses, callouts, dimensions, stamps, notes): new `points` / `rect` / `tip` / `at`, `color`, `width`, `text`. The annotation is rebuilt with the same builder that made it (same id kept where the file allows, so replies and the Comments tab stay attached), and a dimension's length is worked out again from its new ends and the sheet's scale.
- [ ] In the reader, with a shape tool or the arrow tool on, clicking a placed markup selects it: handles on each corner or end (a cloud's or polyline's every vertex, a callout's tip, knee and box, a dimension's two ends, a stamp's or rectangle's corners). Drag a handle to reshape, the body to move, Delete to remove; a dimension's ends snap to the drawing like when it was drawn (Alt: no snapping). Double-click a callout to change its words. The colour swatches and the thickness list change the selected markup.
- [ ] Each change is one step for Undo and Redo, like placing.
- [ ] Markups made by other programs of the same types (a `/Polygon` cloud from Bluebeam, a `/Line` from Acrobat) can be moved and reshaped the same way where their keys are the standard ones; the others are left alone and say so.
- [ ] Tests: each kind changed through the engine keeps its type and keys and reads back at its new place; in a browser, dragging a cloud's vertex and a dimension's end changes the saved file, and the dimension's words follow.

**Task 2b.2: stamps of your own**
- [ ] A list of the user's own stamps, kept in the app's settings: words, colour, with or without name and date. Add, change and remove them from the Stamp list ("Your stamps…"); they show in the list under the built-in ones.
- [ ] Test: a saved green "CHECKED" stamp is offered after a restart and places a `/Stamp` in that colour.

**Task 2b.3: a quantity schedule (takeoff) from dimensions and measurements** (done in 0.43.0)
- [x] `pdfmanager/quantities.py`: `read` (the reader's dimensions and measurements in the PDF, and other programs' `/Measure` / `/IT …Dimension` annotations, each worked out from its points and scale), `schedule` (plus the reader's list, with `totals` per group, kind and unit), `write_csv`, `write_xlsx` (sheets Quantities and Totals, numbers as numbers).
- [x] Columns: sheet, kind, group, name, quantity, unit, perimeter and its unit, the words, the scale, colour, who and when.
- [x] From the Measurements panel (CSV and Excel buttons, `/api/quantities`, the open document as it is now), the tool "Quantities from a drawing", and `pdfm quantities plan.pdf [-o takeoff.xlsx] [--csv]`.
- [x] The scale written with a measurement follows ISO 32000 12.9 (`/D` and `/A` are factors on `/X`'s unit); an area's cut-outs are kept in `/ZPDFHoles`. Files from before are read right.
- [x] Tests: `tests/test_quantities.py`, `tests/test_quantities_ui.py` + `tests/quantitiesui.js`.
- [ ] Left for later: one workbook for several files (a sheet set); areas put in the PDF before 0.43.0 have no cut-outs recorded, so they read as the whole outline; elliptical/arc measurements of other programs; prices per unit.

**Seen in the Windows check of 0.38.0, for a later release:**
- Measurements remembered for a file come back on a copy of it changed by a tool (Rotate): the copies share the PDF's `/ID`. Key the memory on more than the ID (the page count and sizes, or the path), or ask before bringing them back.
- The zoom changes when Annotate & sign opens, and choosing Distance closes the Annotate bar (the page moves both times).
- Once, the Annotate bar closed and the side panel opened after a double-click ended a line (not reproduced).
- A callout's comment popup opens after it's added and stays until a click elsewhere.

### Phase 3 (release C): PDF to DXF

**Task 3.1: vector export**
- [ ] For chosen pages, read the page's paths through the existing content-stream reader that snapping uses (`snap.js` / `pdfmanager` geometry). That covers lines, Bézier curves (straight runs become LINE, true arcs become ARC when they fit within tolerance, the rest become SPLINE or a polyline), rectangles, fills (HATCH with a solid pattern) and text (TEXT/MTEXT with the font's size and angle).
- [ ] Coordinates use the drawing's real units: if the page has a scale (`/VP /Measure` or a calibration the user saved), 1 DXF unit = 1 real unit, and `$INSUNITS` is set. Otherwise points.
- [ ] Each PDF layer becomes a DXF layer. Without layers, group by stroke colour and line width, as CAD exports usually map them. Colours go to true colour; line weights are kept.
- [ ] A "PDF → DXF" tool in Convert, and `pdfm to-dxf`.
- [ ] Test: a reportlab drawing (a rectangle, a circle, a line and text) at a 1:100 scale exports to a DXF that ezdxf reads back with the right entities, the right sizes in metres (±0.5 %) and the text in place.

### Phase 4 (release D): DXF to PDF (and DWG through the user's converter)

**Task 4.1: DXF to PDF**
- [ ] Use ezdxf's drawing add-on with its PDF or SVG backend, or render to the existing reportlab canvas.
- [ ] Choose the layout (model space or a paper-space layout), the paper size, and the plot scale (fit, or 1:50, 1:100 and so on). Line weights come from the DXF, and there's a monochrome choice.
- [ ] DXF layers become PDF layers (optional content), so Phase 1's panel works on the result. Write `/VP /Measure` for the chosen scale, so ZPDF measures it at once.
- [ ] Add DXF to the "Office → PDF" family as "Drawing → PDF", and add `.dxf` to the files the reader opens (converted on open).
- [ ] Test: a DXF made with ezdxf (a 10 m wall on layer WALLS, text on TEXT) at 1:100 on A3 gives a PDF where the wall measures 10 m in ZPDF and both layers are listed.

**Task 4.2: DWG (only if the user installs the ODA File Converter)**
- [ ] If `ODAFileConverter.exe` is found (standard install folders, or a path in Settings), DWG → DXF goes through it, and then Task 4.1 handles the rest. Otherwise DWG files are refused with a plain message saying how to get the converter (it's free from opendesign.com) or to export DXF from the CAD program.
- [ ] Test: with the converter faked (a stub that copies a DXF), a `.dwg` goes through the path. Without it, the message is shown.

### Phase 5 (release E): sheet sets

**Task 5.1: split and name sheets**
- [ ] Find each page's sheet number and title in the title block, bottom right by default, or where the user points once for the set, using pdfium text there and a pattern like "A-101" or "S2.03".
- [ ] "Split into sheets" names each file `A-101 Floor plan.pdf`. "Name pages" sets page labels to the sheet numbers, so the reader's page box shows "A-101".
- [ ] Test: a 3-page set with title blocks splits into three correctly named files.

**Task 5.2: links between sheets**
- [ ] Find references like "3/A-501" or "SEE A-201" (detail/sheet) in the drawing text, and add link annotations to the page labelled with that sheet number.
- [ ] Test: a reference "2/A-102" on sheet A-101 becomes a link that goes to the page labelled A-102.

**Task 5.3: compare sets**
- [ ] Compare two versions of a set sheet by sheet, matched by sheet number, not page order. Use the existing overlay compare, and list which sheets changed (a pixel difference above a threshold), were added or were removed.
- [ ] Test: set v2 with one changed sheet and one new sheet gives a list showing exactly those.

---

## Order and size

| Release | Phase | Size | Risk |
|---|---|---|---|
| A | 1: layers panel, print/save/flatten with layers | small–medium | low |
| B | 2: clouds, callouts, dimensions, arrows, construction stamps | medium | low: standard annotation types |
| B2 | 2b: move and reshape placed markups, your own stamps, quantity schedule to CSV/XLSX | medium | medium: selecting and rebuilding annotations in place |
| C | 3: PDF → DXF | medium | medium: curve fitting, scale |
| D | 4: DXF → PDF, DWG via ODA | medium | medium: a new dependency (ezdxf) |
| E | 5: sheet sets (split/name, links, set compare) | medium | low–medium: title-block detection |

**Place in the roadmap:** after 0.30 (File Explorer actions and the watch folder), alongside the paragraph reflow plan. Interleave them by what you want first; the two touch different code. Each release needs a Windows check with real drawings: an architectural set, a CAD-exported PDF with layers and a DXF from a CAD program.
