# Interactive Form Authoring: Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Design a fillable form in the reader the way Acrobat's "Prepare a form" does: put fields on the page, see and change everything about each one in a panel, set the order people tab through them, try the form out, and change or remove fields a file already has. The result is a standard AcroForm that works in Acrobat, browsers and ZPDF.

**Where it stands (0.33):**
- The **Add form fields** tool (a tool page, not the reader) adds text, multi-line text, checkbox, radio, dropdown, list and signature fields: drag a box or click, **Find fields** detects a printed form's fields (`formdetect.py`), and a field can be moved, resized, renamed or removed before the tool runs (`forms.add_fields`).
- Formats (number, date, phone and so on), calculations and validation can be given per field and are written as the standard Acrobat scripts (`forms.py`: `_format_scripts`, `_calculation`, `_validation`); the reader runs them when filling in.
- Fields already in the file are shown striped and can't be changed.
- The reader fills forms in, and has a "Make fillable" bar that runs detection.

**What's missing:**
1. **Fields already in a file can't be edited** (moved, resized, renamed, retyped, deleted), only added to.
2. **No properties panel:** tooltip, required, read-only, default value, max length, comb, password, multi-line, alignment, font, size, colours, border, choices with export values, format, validation, calculation are either not settable in the app or only by typing.
3. **No designer in the reader:** authoring happens on a tool page with a preview, one run at a time, without Undo, snapping or the page's real zoom.
4. **No layout help:** align, same size, distribute, duplicate, copy across pages, snap to the page's lines.
5. **No tab order** control, and no way to see it.
6. **No buttons or actions:** reset, print, submit by email, go to page, show or hide a field.
7. **No way to try the form** without leaving the designer, and no data round trip (clear, fill from a file, export what was filled in).

## Global constraints

- Standard AcroForm only (ISO 32000): no XFA, nothing that only one program reads. Every field keeps working in Acrobat, Edge, Chrome, Firefox and ZPDF's own reader; a test opens each result with pdf.js and pdfium and checks names, types, values and appearance streams.
- JavaScript in forms is limited to the standard format, validate and calculate scripts ZPDF already writes, plus simple show/hide and reset actions. No free-form script editor by default (it would be the one place ZPDF writes arbitrary code into a file); an "Advanced" box for a custom script is a later decision for the owner.
- Signed files: changing fields breaks a signature. The designer says so before the first change and offers to work on a copy.
- PDF/A and tagged files stay valid: fields get `/TU` (the tooltip is the accessible name), a place in the structure tree when the file is tagged, and embedded fonts in PDF/A.
- Every change is one Undo step, as in the rest of the reader. Light, dark and forced-colors themes; all of it reachable by keyboard. Python 3.10 to 3.14, Linux and Windows. No model identifiers in code or commits.

---

### Phase 1 (release A): change the fields a file has

**Task 1.1: the engine**
- [ ] `forms.edit_fields(path, output, changes)`: move or resize a widget, rename a field (keeping hierarchy and unique names), change its type where that makes sense (text ↔ multi-line; checkbox ↔ radio), delete a field or one widget of it, and set any property of Phase 2. Appearance streams are rebuilt; `/CO` (calculation order) and scripts that name a renamed field are updated.
- [ ] `forms.existing_fields` returns everything the panel shows: rect, type, name, tooltip, flags, default and current value, choices, format, calculation, validation, font, colours, tab index.
- [ ] Tests: each change on a form made by ZPDF and on forms made by Acrobat, LibreOffice and Word (fixtures built with pikepdf to match their structures: kids with shared parents, widgets merged with fields, inherited flags).

**Task 1.2: in the tool page**
- [ ] The striped "already in the file" fields become editable like new ones. Deleting asks first when the field has a value or is used by another field's calculation.

### Phase 2 (release B): the designer in the reader

**Task 2.1: Form mode**
- [ ] "Prepare form" in the reader (Tools, the command palette, and the "Make fillable" bar): a mode like Annotate, with a bar of field types (text, multi-line, checkbox, radio, dropdown, list, date, signature, button) and the fields drawn over the page with their names.
- [ ] Click or drag to add; click to select; drag to move; handles to resize; arrow keys nudge (Shift: larger steps); Delete removes; Ctrl+D duplicates; Ctrl+C and Ctrl+V copy, also to another page; Shift+click and a drag box select several.
- [ ] Each change goes through the reader's change path, so Undo, Redo, the unsaved dot and Save work as everywhere else. Changes are batched: a drag is one request when the pointer is released.
- [ ] **Find fields** runs from the bar and adds what it finds as ordinary, editable fields.

**Task 2.2: the properties panel** (the file panel on the right, when a field is selected)
- [ ] **General:** name, tooltip, required, read-only, hidden / visible but not printed.
- [ ] **Look:** font (the document's, standard, installed: the text editor's font list), size or auto, text colour, fill, border colour and width, alignment.
- [ ] **Text fields:** default value, max length, comb (one box per character), multi-line, password, scroll.
- [ ] **Choices:** the list with a shown value and an export value each, reorder by dragging, default choice, allow typing a new value (dropdown), allow several (list).
- [ ] **Checkbox and radio:** tick style (check, cross, dot, square), export value, checked by default, the group a radio belongs to.
- [ ] **Format:** none, number (decimals, separator, currency, negative style), percent, date (pattern), time, phone, postcode, a custom mask. **Validation:** a range, or a pattern. **Calculation:** sum, product, average, minimum, maximum of chosen fields (picked by clicking them on the page), or a simple formula over field names (`Qty * Price`), checked before it's written.
- [ ] With several fields selected, the panel changes what they share.
- [ ] Test (browser): each property set in the panel reads back from the saved file and behaves when filling in (required marks, a comb field, a currency format, a sum).

**Task 2.3: layout help**
- [ ] Guides while dragging: edges and centres of other fields, and the page's own lines (the snapping geometry measuring uses).
- [ ] Align left, right, top, bottom, centre; same width, height or both; distribute evenly; with several selected.
- [ ] "Copy to pages…": the same field on a range of pages (same name: one value everywhere; or numbered names).
- [ ] A table of fields: select a field, "Make a table" with rows and columns, names numbered (`Qty.1`, `Qty.2`).

### Phase 3 (release C): order, trying it out, and data

**Task 3.1: tab order**
- [ ] A numbered badge on each field shows the order. Default: rows, left to right, top to bottom (the structure order when the file is tagged).
- [ ] A "Fields" list in the sidebar (page by page): drag to reorder, click to select, rename in place. "Order by rows" and "Order by columns" reset it. Written as the page's annotation order and `/Tabs`.
- [ ] Test: Tab in pdf.js and the order pdfium reports follow the list.

**Task 3.2: Preview**
- [ ] A "Preview" switch in Form mode fills the form in as a user would, with scripts running, without leaving the designer; values typed in a preview aren't kept unless "Keep as default values" is chosen.
- [ ] A check list before finishing: fields without a tooltip, duplicate names with different types, fields off the page, overlapping fields, required fields that are read-only, calculations that name a missing field.

**Task 3.3: data**
- [ ] Clear the form; export what was filled in (FDF, XFDF, CSV, JSON); fill from such a file. A "Fill many" tool: one PDF per row of a spreadsheet (mail merge), named from a column.
- [ ] Test: export then import gives the same values; ten rows give ten filled files.

### Phase 4 (release D): buttons and simple actions

- [ ] Button fields with a label and look, and one of these actions: reset the form (all or chosen fields), print, go to a page, open a web link, send by email (`mailto:` with the form's data as FDF or the whole PDF; no server).
- [ ] Show or hide fields: "when this box is ticked, show these fields", written as the standard field actions and checked in ZPDF's reader and pdf.js.
- [ ] Barcode of the form's data (a QR code field that updates in ZPDF and Acrobat) is a decision for the owner; left out unless asked for.
- [ ] Test: each action type saved, reopened, and triggered in the reader.

---

## Order and size

| Release | Phase | Size | Risk |
|---|---|---|---|
| A | 1: edit existing fields (engine, tool page) | medium | medium: the many ways programs structure fields |
| B | 2: designer in the reader, properties panel, layout help | large | medium: a new mode in the reader |
| C | 3: tab order, preview and checks, data in and out, fill many | medium | low |
| D | 4: buttons, show/hide, email | medium | medium: what each reader supports |

**Place in the roadmap:** release A is useful by itself and small enough to go between other releases. B is the main one, and fits after paragraph reflow B, since both build on the reader's editing modes. Each release needs a Windows check, and the results opened in Acrobat Reader, Edge and Chrome.

**Decisions for the owner:**
- whether an "Advanced" custom script box is wanted (off in this plan);
- whether email submission should attach the whole PDF or only the data;
- whether barcode fields are wanted.
