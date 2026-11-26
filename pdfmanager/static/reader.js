/* Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE. */
// PDF reader built on pdf.js (bundled in /static/pdfjs, see packaging/update_pdfjs.py).
//
//   const reader = new Reader(hostElement, { token, isDesktop, api, tools, onEdit });
//   await reader.openFile(file, password?)          // a File from a drop or file picker
//   await reader.openBytes(bytes, name, password?)  // raw bytes
// A password that turns out wrong (or none, for a protected file) prompts.
//
// Features: continuous scrolling, page navigation, zoom (presets, Ctrl+wheel,
// pinch), rotation, single/two-page view, thumbnails, outline (bookmarks),
// find with highlighting, text selection, links, form filling, annotating
// (highlight, text, drawing, pictures) and signing, document properties,
// hand tool, dark pages, full screen, printing, saving a copy and handing the
// document to one of the app's tools.
//
// Reader is one object; its parts are in files of their own, each a class
// Reader extends (see Measuring in measuring.js): tabs.js (the open documents,
// switching, closing, the tab bar and the save state shown), annotate.js,
// sign.js, find.js, textedit-ui.js (the text editor box) and measuring.js.
// A tab's state is a TabState (tabstate.js); what the modules share is in
// readerkit.js. Here: building the reader, keys, opening and showing a
// document, the sidebar, saving, printing, pages, dialogs.

import * as files from "./files.js";
import { COLORS, el, fadeOut, heldTimer, ICON, isTyping, MIN_ZOOM, MAX_ZOOM, pdfjs, store, svg, viewer } from "./readerkit.js";
import { Annotating } from "./annotate.js";
import { CheckOcrMode } from "./checkocr.js";
import { CropMode } from "./cropmode.js";
import { attachMobileTouch } from "./mobile-touch.js";
import { openAiAssistant } from "./ai-assistant.js";
import { Finding } from "./find.js";
import { FormMode } from "./formmode.js";
import { Layering } from "./layers.js";
import { MangaLens } from "./manga-lens.js";
import { MarkupEditing } from "./markedit.js";
import { Measuring } from "./measuring.js";
import { ReaderNav } from "./reader-nav.js";
import { BROKEN_WHY, ReaderRender } from "./reader-render.js";
import { ReaderSave } from "./reader-save.js";
import { Signing } from "./sign.js";
import { SplitView } from "./splitview.js";
import { Tabs } from "./tabs.js";
import { TextEditing } from "./textedit-ui.js";
export { heldTimer, loadPdfjs } from "./readerkit.js";


/** What a protected PDF allows, from pdf.js's list of allowed permissions. */
function permissionText(allowed) {
  const P = pdfjs.PermissionFlag, has = (f) => allowed.includes(f);
  const printing = has(P.PRINT_HIGH_QUALITY) ? "printing" : has(P.PRINT) ? "low-quality printing only" : "no printing";
  const changes = has(P.MODIFY_CONTENTS) ? "any changes" : has(P.MODIFY_ANNOTATIONS) ? "commenting, filling in forms and signing"
    : has(P.FILL_INTERACTIVE_FORMS) ? "filling in forms and signing" : has(P.ASSEMBLE) ? "inserting, deleting and turning pages" : "no changes";
  return `${printing}, ${changes}, ${has(P.COPY) ? "copying allowed" : "no copying"}`;
}



// Rubber stamps: their words (markup.py draws them in their colours).
const STAMPS = ["APPROVED", "NOT APPROVED", "REVIEWED", "RECEIVED", "DRAFT", "FINAL", "CONFIDENTIAL",
  "FOR COMMENT", "VOID"];
// The answers to a submittal (drawings sent for review).
const REVIEW_STAMPS = ["APPROVED AS NOTED", "REVISE AND RESUBMIT", "REJECTED", "FOR INFORMATION", "NOT FOR CONSTRUCTION"];
const SHAPES = [["rect", "Rectangle"], ["ellipse", "Ellipse"], ["cloud", "Cloud"], ["arrow", "Arrow"], ["line", "Line"]];
// For drawings: a cloud or a line through the points clicked, a dimension
// with the real length, words with a pointer.
const DRAWING_SHAPES = [["polycloud", "Cloud, any shape"], ["polyline", "Line with corners"], ["polyarrow", "Arrow with corners"],
  ["dimension", "Dimension"], ["callout", "Callout"]];
// A field's script that is only calls of Acrobat's form functions with plain
// values (numbers, quoted text, true or false, a list of quoted texts): how
// forms format and add up. The functions are these by name and nothing can
// hide in an argument. The pattern is risks.py's FORM_SCRIPT_SOURCE, kept
// the same by tests/test_risks.py, so the risk check and the reader agree
// on what is harmless.
const FORM_SCRIPT = new RegExp("^[ \\t\\n\\r]*(?:(?:AFNumber_Format|AFNumber_Keystroke|AFPercent_Format|AFPercent_Keystroke|AFDate_Format|AFDate_FormatEx|AFDate_Keystroke|AFDate_KeystrokeEx|AFTime_Format|AFTime_FormatEx|AFTime_Keystroke|AFTime_KeystrokeEx|AFSpecial_Format|AFSpecial_Keystroke|AFSpecial_KeystrokeEx|AFRange_Validate|AFSimple_Calculate)[ \\t\\n\\r]*\\([ \\t\\n\\r]*(?:(?:(?:\"(?:[^\"\\\\\\n\\r]|\\\\[^\\n\\r])*\"|'(?:[^'\\\\\\n\\r]|\\\\[^\\n\\r])*')|-?[0-9]+(?:\\.[0-9]+)?(?:[eE][+-]?[0-9]+)?|true|false|new[ \\t\\n\\r]+Array[ \\t\\n\\r]*\\([ \\t\\n\\r]*(?:(?:\"(?:[^\"\\\\\\n\\r]|\\\\[^\\n\\r])*\"|'(?:[^'\\\\\\n\\r]|\\\\[^\\n\\r])*')(?:[ \\t\\n\\r]*,[ \\t\\n\\r]*(?:\"(?:[^\"\\\\\\n\\r]|\\\\[^\\n\\r])*\"|'(?:[^'\\\\\\n\\r]|\\\\[^\\n\\r])*'))*)?[ \\t\\n\\r]*\\))(?:[ \\t\\n\\r]*,[ \\t\\n\\r]*(?:(?:\"(?:[^\"\\\\\\n\\r]|\\\\[^\\n\\r])*\"|'(?:[^'\\\\\\n\\r]|\\\\[^\\n\\r])*')|-?[0-9]+(?:\\.[0-9]+)?(?:[eE][+-]?[0-9]+)?|true|false|new[ \\t\\n\\r]+Array[ \\t\\n\\r]*\\([ \\t\\n\\r]*(?:(?:\"(?:[^\"\\\\\\n\\r]|\\\\[^\\n\\r])*\"|'(?:[^'\\\\\\n\\r]|\\\\[^\\n\\r])*')(?:[ \\t\\n\\r]*,[ \\t\\n\\r]*(?:\"(?:[^\"\\\\\\n\\r]|\\\\[^\\n\\r])*\"|'(?:[^'\\\\\\n\\r]|\\\\[^\\n\\r])*'))*)?[ \\t\\n\\r]*\\)))*)?[ \\t\\n\\r]*\\)|event\\.value[ \\t\\n\\r]*=[ \\t\\n\\r]*(?:\\([ \\t\\n\\r]*)*[+-]?[ \\t\\n\\r]*(?:Number[ \\t\\n\\r]*\\([ \\t\\n\\r]*this\\.getField[ \\t\\n\\r]*\\([ \\t\\n\\r]*(?:\"(?:[^\"\\\\\\n\\r]|\\\\[^\\n\\r])*\"|'(?:[^'\\\\\\n\\r]|\\\\[^\\n\\r])*')[ \\t\\n\\r]*\\)\\.value[ \\t\\n\\r]*\\)|-?[0-9]+(?:\\.[0-9]+)?(?:[eE][+-]?[0-9]+)?)(?:[ \\t\\n\\r]*\\))*(?:[ \\t\\n\\r]*[+*/-][ \\t\\n\\r]*(?:\\([ \\t\\n\\r]*)*[+-]?[ \\t\\n\\r]*(?:Number[ \\t\\n\\r]*\\([ \\t\\n\\r]*this\\.getField[ \\t\\n\\r]*\\([ \\t\\n\\r]*(?:\"(?:[^\"\\\\\\n\\r]|\\\\[^\\n\\r])*\"|'(?:[^'\\\\\\n\\r]|\\\\[^\\n\\r])*')[ \\t\\n\\r]*\\)\\.value[ \\t\\n\\r]*\\)|-?[0-9]+(?:\\.[0-9]+)?(?:[eE][+-]?[0-9]+)?)(?:[ \\t\\n\\r]*\\))*)*)(?:[ \\t\\n\\r]*;[ \\t\\n\\r]*(?:(?:AFNumber_Format|AFNumber_Keystroke|AFPercent_Format|AFPercent_Keystroke|AFDate_Format|AFDate_FormatEx|AFDate_Keystroke|AFDate_KeystrokeEx|AFTime_Format|AFTime_FormatEx|AFTime_Keystroke|AFTime_KeystrokeEx|AFSpecial_Format|AFSpecial_Keystroke|AFSpecial_KeystrokeEx|AFRange_Validate|AFSimple_Calculate)[ \\t\\n\\r]*\\([ \\t\\n\\r]*(?:(?:(?:\"(?:[^\"\\\\\\n\\r]|\\\\[^\\n\\r])*\"|'(?:[^'\\\\\\n\\r]|\\\\[^\\n\\r])*')|-?[0-9]+(?:\\.[0-9]+)?(?:[eE][+-]?[0-9]+)?|true|false|new[ \\t\\n\\r]+Array[ \\t\\n\\r]*\\([ \\t\\n\\r]*(?:(?:\"(?:[^\"\\\\\\n\\r]|\\\\[^\\n\\r])*\"|'(?:[^'\\\\\\n\\r]|\\\\[^\\n\\r])*')(?:[ \\t\\n\\r]*,[ \\t\\n\\r]*(?:\"(?:[^\"\\\\\\n\\r]|\\\\[^\\n\\r])*\"|'(?:[^'\\\\\\n\\r]|\\\\[^\\n\\r])*'))*)?[ \\t\\n\\r]*\\))(?:[ \\t\\n\\r]*,[ \\t\\n\\r]*(?:(?:\"(?:[^\"\\\\\\n\\r]|\\\\[^\\n\\r])*\"|'(?:[^'\\\\\\n\\r]|\\\\[^\\n\\r])*')|-?[0-9]+(?:\\.[0-9]+)?(?:[eE][+-]?[0-9]+)?|true|false|new[ \\t\\n\\r]+Array[ \\t\\n\\r]*\\([ \\t\\n\\r]*(?:(?:\"(?:[^\"\\\\\\n\\r]|\\\\[^\\n\\r])*\"|'(?:[^'\\\\\\n\\r]|\\\\[^\\n\\r])*')(?:[ \\t\\n\\r]*,[ \\t\\n\\r]*(?:\"(?:[^\"\\\\\\n\\r]|\\\\[^\\n\\r])*\"|'(?:[^'\\\\\\n\\r]|\\\\[^\\n\\r])*'))*)?[ \\t\\n\\r]*\\)))*)?[ \\t\\n\\r]*\\)|event\\.value[ \\t\\n\\r]*=[ \\t\\n\\r]*(?:\\([ \\t\\n\\r]*)*[+-]?[ \\t\\n\\r]*(?:Number[ \\t\\n\\r]*\\([ \\t\\n\\r]*this\\.getField[ \\t\\n\\r]*\\([ \\t\\n\\r]*(?:\"(?:[^\"\\\\\\n\\r]|\\\\[^\\n\\r])*\"|'(?:[^'\\\\\\n\\r]|\\\\[^\\n\\r])*')[ \\t\\n\\r]*\\)\\.value[ \\t\\n\\r]*\\)|-?[0-9]+(?:\\.[0-9]+)?(?:[eE][+-]?[0-9]+)?)(?:[ \\t\\n\\r]*\\))*(?:[ \\t\\n\\r]*[+*/-][ \\t\\n\\r]*(?:\\([ \\t\\n\\r]*)*[+-]?[ \\t\\n\\r]*(?:Number[ \\t\\n\\r]*\\([ \\t\\n\\r]*this\\.getField[ \\t\\n\\r]*\\([ \\t\\n\\r]*(?:\"(?:[^\"\\\\\\n\\r]|\\\\[^\\n\\r])*\"|'(?:[^'\\\\\\n\\r]|\\\\[^\\n\\r])*')[ \\t\\n\\r]*\\)\\.value[ \\t\\n\\r]*\\)|-?[0-9]+(?:\\.[0-9]+)?(?:[eE][+-]?[0-9]+)?)(?:[ \\t\\n\\r]*\\))*)*))*[ \\t\\n\\r]*;?[ \\t\\n\\r]*$");
const FORM_SCRIPT_MAX = 2000;

/** ``doc`` (pdf.js's) as the scripting sandbox may see it: without the
 *  document's own scripts and its pages' open and close actions, and with
 *  only the field scripts that are form functions and nothing else. So a
 *  PDF's scripts are never run here; a form still formats and adds up. */
function formScriptsOnly(doc) {
  const bound = (target, prop) => { const v = target[prop]; return typeof v === "function" ? v.bind(target) : v; };
  const none = async () => null;
  const quietPage = (page) => new Proxy(page, { get: (t, p) => (p === "getJSActions" ? none : bound(t, p)) });
  const fields = async () => {
    const all = await doc.getFieldObjects();
    if (!all) return all;
    const clean = (list) => list.map((field) => {
      if (!field?.actions) return field;
      const isMap = field.actions instanceof Map, actions = new Map();
      for (const [when, scripts] of (isMap ? field.actions : Object.entries(field.actions))) {
        const kept = (Array.isArray(scripts) ? scripts : [scripts]).filter((js) => typeof js === "string" && js.length <= FORM_SCRIPT_MAX && FORM_SCRIPT.test(js));
        if (kept.length) actions.set(when, kept);
      }
      return { ...field, actions: !actions.size ? null : isMap ? actions : Object.fromEntries(actions) };
    });
    // (pdf.js gives Maps, from each name to its fields and from each event
    // to its scripts; older versions gave plain objects.)
    if (all instanceof Map) return new Map([...all].map(([name, list]) => [name, clean(list)]));
    return Object.fromEntries(Object.entries(all).map(([name, list]) => [name, clean(list)]));
  };
  return new Proxy(doc, { get: (t, p) => (p === "getJSActions" ? none : p === "getFieldObjects" ? fields
    : p === "getPage" ? async (n) => quietPage(await t.getPage(n)) : bound(t, p)) });
}


const ZOOM_PRESETS = [
  ["auto", "Automatic"], ["page-fit", "Fit page"], ["page-width", "Fit width"], ["page-actual", "Actual size"], ["true-size", "True paper size"],
  ["0.5", "50%"], ["0.75", "75%"], ["1", "100%"], ["1.25", "125%"], ["1.5", "150%"], ["2", "200%"], ["3", "300%"], ["4", "400%"],
];


/** Single keys for the annotate bar's labelled tools, while it's open (n, p, r,
 *  t, e and m are the reader's own: next, previous, rotate, panel, edit text, measure). */
const ANNOT_KEYS = { h: "HIGHLIGHT", c: "NOTE", a: "FREETEXT", d: "INK", s: "SIGN" };

export class Reader extends Tabs(CheckOcrMode(CropMode(FormMode(Finding(Signing(Annotating(MarkupEditing(TextEditing(Measuring(Layering(SplitView(ReaderNav(ReaderRender(ReaderSave(Object))))))))))))))) {
  constructor(host, { token, isDesktop, api, tools = [], onEdit, onAllTools, onCommands, onHome, onChange, recent, onFormHint, onFormSelect, onFormCheck, onRepair, onForgetRecent, onSaveTab, onUnsaved, onBrokenWhy, onOpenWithId } = {}) {
    super();
    this.host = host;
    this.token = token;
    this.isDesktop = isDesktop || (() => false);
    this.api = api || (() => null);
    this.tools = tools;
    this.onEdit = onEdit;
    this.onAllTools = onAllTools;
    this.onCommands = onCommands;
    this.onHome = onHome;
    this.onChange = onChange;  // the open documents or the shown one changed (the app's file panel follows)
    this.onUnsaved = onUnsaved;  // (names) => the tabs with unsaved work are these now (closing the window asks)
    this.recent = recent;  // (host) => fills host with the app's Recent list (the start screen's), resolves to how many
    this.onFormHint = onFormHint;  // (tab) => the "looks like a form" check came out for tab, or was dismissed
    this.onFormSelect = onFormSelect;  // (selected, pages, tab) => the selected fields in Prepare form changed
    this.onFormCheck = onFormCheck;    // (issues, tab) => results of "Check form" (or null to clear)
    this.onBrokenWhy = onBrokenWhy;    // (tab) => what's known of a file that can't be shown ({ for_people } when it's protected for certificates)
    this.onOpenWithId = onOpenWithId;  // (tab, Digital ID file, its password) => "" once the opened copy is in a tab, else what went wrong
    this.onRepair = onRepair;  // (tab) => run Repair on a file that can't be shown; resolves when its result is open (or it failed)
    this.onForgetRecent = onForgetRecent;  // (tab) => take a file that can't be shown off the Recent list
    this.onSaveTab = onSaveTab;  // (tab) => save a tab the app made (a tool's result): resolves true once saved
    // Everything that changes which document is shown runs one after the
    // other (see #serial): two loads at once would show the wrong tab or
    // trip over the shared worker.
    this.chain = Promise.resolve();
    this.doc = null;
    this.tabs = [];          // open documents (see openBytes)
    this.activeTab = null;
    this.bytes = null;       // the shown document's bytes
    this.name = "";
    this.password = null;
    this.active = false;
    this.#build();
    this.setPageTheme(this.pageTheme());
    attachMobileTouch(this);
  }

  // ---- DOM ------------------------------------------------------------------
  #build() {
    const b = (name, title, attrs = {}) => el("button", { class: "rd-btn", type: "button", title, "aria-label": title, html: svg(name), ...attrs });
    this.root = el("div", { class: "rd", tabindex: "-1" });

    // Toolbar
    this.sidebarBtn = b("sidebar", "Show or hide the sidebar", { "aria-pressed": "true", disabled: true });
    // The document's name, and a dot (with the word, for screen readers) while it has changes not saved.
    this.titleText = el("span", { class: "rd-title-text" });
    this.titleDot = el("span", { class: "rd-dot", title: "Not saved", hidden: true });
    this.titleSr = el("span", { class: "rd-sr", hidden: true }, ", unsaved");
    this.titleEl = el("div", { class: "rd-title" }, this.titleText, this.titleDot, this.titleSr);
    this.prevBtn = b("up", "Previous page", { disabled: true });
    this.nextBtn = b("down", "Next page", { disabled: true });
    this.pageInput = el("input", { type: "text", inputmode: "numeric", "aria-label": "Page number", disabled: true, autocomplete: "off" });
    this.pageCount = el("span", {}, "");
    this.zoomOutBtn = b("minus", "Zoom out (Ctrl −)", { disabled: true });
    this.zoomInBtn = b("plus", "Zoom in (Ctrl +)", { disabled: true });
    this.zoomInput = el("input", { type: "text", class: "rd-zoominput", inputmode: "decimal", "aria-label": "Zoom (type a percentage)",
      title: "Zoom: type a percentage and press Enter; ↑ ↓ change it by 10% (with Shift, 1%)", autocomplete: "off", disabled: true });
    this.zoomMenuBtn = b("down", "Zoom presets", { class: "rd-btn rd-zoommenu-btn", "aria-haspopup": "menu", "aria-expanded": "false", disabled: true });
    this.zoomMenu = el("div", { class: "rd-menu rd-zoommenu", role: "menu", "aria-label": "Zoom", hidden: true });
    this.zoomPreset = "auto";  // the preset the zoom is at, or "custom"
    this.scaleBtn = el("button", { class: "rd-btn rd-scale-btn", type: "button", disabled: true, "aria-expanded": "false",
      title: "Drawing scale, true-size zoom and measuring (M to measure)", html: svg("ruler") + "<span>Scale</span>" });
    this.scaleLabel = this.scaleBtn.querySelector("span");
    // The file panel at the right (the app fills it: this file's facts and tools).
    this.panelBtn = b("panel", "File panel", { class: "rd-btn rd-panel-btn", "aria-expanded": "false", "aria-controls": "rd-side" });
    this.panelBtn.title = "Show or hide the file panel (T)";
    // Changes not saved yet: Save and Undo come out where they're seen.
    this.saveNowBtn = el("button", { class: "rd-btn primary seal rd-savenow", type: "button", hidden: true,
      title: "Save your changes (Ctrl+S)", html: svg("save") + "<span>Save</span>" });
    this.undoNowBtn = b("undo", "Undo the last change (Ctrl+Z)", { hidden: true });
    this.homeBtn = this.onHome ? b("home", "Home: all of ZPDF's tools (your documents stay open)") : null;

    const t = (name, label, title, attrs = {}) => el("button", { class: "rd-btn rd-labelled", type: "button", title, html: svg(name) + `<span>${label}</span>`, ...attrs });
    this.findBtn = b("search", "Find in document (Ctrl+F)", { "aria-pressed": "false" });
    this.annotateBtn = t("pen", "Annotate & sign", "Annotate and sign", { "aria-pressed": "false" });
    this.moreBtn = b("more", "More", { "aria-haspopup": "menu", "aria-expanded": "false" });

    // The toolbar keeps what reading needs; the More menu has the rest, and
    // the file panel this file's tools.
    const toolbar = el("div", { class: "rd-toolbar", role: "toolbar", "aria-label": "Reader" },
      el("div", { class: "rd-group" }, this.homeBtn, this.sidebarBtn),
      this.titleEl,
      el("div", { class: "rd-group" }, this.prevBtn, this.nextBtn,
        el("label", { class: "rd-page" }, this.pageInput, this.pageCount)),
      el("span", { class: "rd-sep" }),
      el("div", { class: "rd-group rd-zoomgroup" }, this.zoomOutBtn, this.zoomInput, this.zoomMenuBtn, this.zoomInBtn),
      el("span", { class: "rd-sep" }),
      this.undoNowBtn, this.saveNowBtn, this.scaleBtn,
      el("div", { class: "rd-group" }, this.findBtn, this.annotateBtn, this.moreBtn, this.panelBtn));

    this._buildFind();  // the find bar (find.js)

    // Annotation bar: the tools used most keep their words (and a single key
    // each, see ANNOT_KEYS); the rest are in the labelled More tools menu.
    const tool = (name, label, mode) => el("button", { class: "rd-btn rd-tool", type: "button", title: label, "aria-pressed": "false", "data-mode": mode, html: svg(name) + `<span>${label}</span>` });
    this.annotTools = [
      tool("pointer", "Select", "NONE"), tool("highlight", "Highlight", "HIGHLIGHT"),
      tool("underline", "Underline", "UNDERLINE"), tool("strike", "Strike out", "STRIKEOUT"),
      tool("note", "Note", "NOTE"), tool("text", "Add text", "FREETEXT"),
      tool("draw", "Draw", "INK"), tool("shapes", "Shapes", "SHAPE"), tool("stamp", "Stamp", "STAMPS"), tool("image", "Add picture", "STAMP"),
      tool("edittext", "Edit text", "EDITTEXT"), tool("imgedit", "Edit pictures", "IMAGES"), tool("link", "Links", "LINKS"),
    ];
    const tip = (mode, title) => { this.annotTools.find((b) => b.dataset.mode === mode).title = title; };
    this.annotTools.find((b) => b.dataset.mode === "NONE").classList.add("compact");
    tip("NONE", "Select: no tool, pick and move annotations (Esc)");
    tip("HIGHLIGHT", "Highlight text: drag over the words (H)");
    tip("FREETEXT", "Add text: click where the words go, then type (A)");
    tip("INK", "Draw freehand on the page (D)");
    tip("STAMP", "Add a picture: choose an image file, then place it");
    tip("UNDERLINE", "Underline text: select the words (or select them first, then click here)");
    tip("STRIKEOUT", "Strike out text: select the words (or select them first, then click here)");
    tip("NOTE", "Add a note: click where it goes, then type your comment (C)");
    tip("SHAPE", "Draw a rectangle, ellipse, cloud, arrow or line: drag on the page (Shift: square, circle, 45°). For drawings: a cloud or line through the points you click, a dimension, a callout");
    tip("IMAGES", "Edit the pictures on the page: drag to move, drag a corner to resize (Shift: freely), replace or delete");
    tip("LINKS", "Edit links: click one to change where it goes or delete it; drag a box to add one");
    tip("STAMPS", "Stamp the page (Approved, Draft…): click where it goes, or drag its size");
    this.stampSelect = el("select", { class: "rd-zoom rd-wide", "aria-label": "Stamp", hidden: true });
    this.stampNames = { usual: STAMPS, review: REVIEW_STAMPS };
    this._fillStamps(store.get("pdfm-stamp", "APPROVED"));   // (the stamps of your own come in a moment: _loadStamps)
    this.stampDated = el("input", { type: "checkbox", checked: store.get("pdfm-stamp-dated", true) });
    this.stampDatedLabel = el("label", { class: "rd-check", hidden: true, title: "Add your name and the date and time under the words" },
      this.stampDated, "Name and date");
    this.shapeSelect = el("select", { class: "rd-zoom rd-wide", "aria-label": "Shape", hidden: true },
      ...SHAPES.map(([v, label]) => el("option", { value: v }, label)),
      el("optgroup", { label: "For drawings" }, ...DRAWING_SHAPES.map(([v, label]) => el("option", { value: v }, label))));
    this.shapeSelect.value = store.get("pdfm-shape", "rect");
    tip("EDITTEXT", "Change words on the page: click a line of text (E)");
    this.signBtn = el("button", { class: "rd-btn rd-tool", type: "button", title: "Add your signature (S)", html: svg("sign") + "<span>Sign</span>" });
    this.certSignBtn = el("button", { class: "rd-btn rd-tool compact", type: "button", "data-mode": "CERTSIGN", "aria-pressed": "false",
      title: "Digital ID: sign with a certificate (.pfx or .p12); drag a box where the signature goes", html: svg("badge") + "<span>Digital ID</span>" });
    this.annotTools.push(this.certSignBtn);
    this._buildRedact();  // reader redaction
    this.swatches = COLORS.map(([name, ink, mark], i) => el("button", { class: "rd-swatch", type: "button", title: name, "aria-label": name, "aria-pressed": String(i === 1),
      style: `--ink:${ink};--mark:${mark}`, "data-index": i }));
    this.sizeSelect = el("select", { class: "rd-zoom", "aria-label": "Line thickness or text size" });
    this.undoBtn = b("undo", "Undo (Ctrl+Z)", { disabled: true });
    this.redoBtn = b("redo", "Redo (Ctrl+Y)", { disabled: true });
    this.deleteBtn = b("trash", "Delete the selected annotation (Delete)", { disabled: true });
    this.annotSaveBtn = el("button", { class: "rd-btn rd-annotsave", type: "button", title: "Nothing to save yet (Ctrl+S)", html: svg("save") + "<span>Save</span>" });
    this.annotCloseBtn = b("x", "Close the annotation tools");
    // More tools: a menu of the rest; the one in use shows on its button.
    const byMode = (mode) => this.annotTools.find((t) => t.dataset.mode === mode);
    const inMenu = [["Mark text", ["UNDERLINE", "STRIKEOUT"]], ["Add", ["SHAPE", "STAMPS", "STAMP"]],
      ["Change", ["EDITTEXT", "IMAGES", "LINKS"]], ["Protect", ["CERTSIGN", "REDACT"]]];
    this.moreTools = inMenu.flatMap(([, modes]) => modes.map(byMode));
    for (const item of this.moreTools) {
      item.classList.remove("compact");
      item.setAttribute("role", "menuitemradio");
      item.setAttribute("aria-checked", "false");
      item.removeAttribute("aria-pressed");
      item.tabIndex = -1;
    }
    this.moreToolsMenu = el("div", { class: "rd-menu rd-toolsmenu", role: "menu", "aria-label": "More tools", hidden: true },
      ...inMenu.flatMap(([label, modes], i) => [...(i ? [el("hr")] : []), el("div", { class: "label", "aria-hidden": "true" }, label), ...modes.map(byMode)]));
    this.moreToolsBtn = el("button", { class: "rd-btn rd-tool rd-moretools", type: "button", "aria-haspopup": "menu", "aria-expanded": "false",
      title: "More tools: underline, shapes, stamps, pictures, links, Digital ID, redact", html: svg("more") + "<span>More tools</span>" + svg("down") });
    this.annotbar = el("div", { class: "rd-findbar rd-annotbar", hidden: true, role: "toolbar", "aria-label": "Annotate" },
      ...["NONE", "HIGHLIGHT", "NOTE", "FREETEXT", "INK"].map(byMode), this.signBtn, this.moreToolsBtn,
      el("span", { class: "rd-sep" }),
      el("div", { class: "rd-swatches", role: "group", "aria-label": "Colour" }, ...this.swatches), this.shapeSelect, this.stampSelect, this.stampDatedLabel, this.sizeSelect,
      this.redactHow, this.redactApplyBtn,
      el("span", { class: "rd-sep" }), this.undoBtn, this.redoBtn, this.deleteBtn,
      el("span", { style: "flex:1" }), this.annotSaveBtn, this.annotCloseBtn);

    this._buildScaleBar();
    this._buildFormBar();
    this._buildCheckOcrBar();

    // Sidebar
    this.thumbsTab = el("button", { class: "rd-tab", type: "button", role: "tab", "aria-selected": "true" }, "Pages");
    this.outlineTab = el("button", { class: "rd-tab", type: "button", role: "tab", "aria-selected": "false", disabled: true }, "Bookmarks");
    this.commentsTab = el("button", { class: "rd-tab", type: "button", role: "tab", "aria-selected": "false" }, "Comments");
    this.authorInput = el("input", { type: "text", class: "rd-author", placeholder: "Your name", "aria-label": "Your name on comments",
      value: store.get("pdfm-author", "") });
    this.commentsList = el("div", { class: "rd-comments", "aria-live": "polite" });
    // -- comment summary and XFDF --
    const commentBtn = (label, title, fn) => el("button", { type: "button", class: "rd-linkbtn", title, onclick: fn }, label);
    // (The file box isn't put on the page: the reader has one file box of its own.)
    const pickXfdf = () => {
      const input = el("input", { type: "file", accept: ".xfdf,.xml" });
      input.addEventListener("change", () => { if (input.files[0]) this._importComments(input.files[0]); });
      input.click();
    };
    const commentActions = el("div", { class: "rd-commentactions" },
      commentBtn("Summary", "A PDF listing every comment, page by page", () => this._commentSummary()),
      commentBtn("Export", "Save the comments as an XFDF file, to send or to add to another copy", () => this._exportComments()),
      commentBtn("Import…", "Add comments from an XFDF file (from Acrobat or another copy)", pickXfdf));
    // -- end of comment summary and XFDF --
    this.commentsPanel = el("div", { class: "rd-panel rd-commentspanel", role: "tabpanel", hidden: true },
      el("label", { class: "rd-authorrow" }, "Comment as", this.authorInput), commentActions, this.commentsList);
    this.thumbsList = el("ol", { class: "rd-thumbs", role: "listbox", "aria-multiselectable": "true", "aria-label": "Pages" });
    this.pickedPages = new Set();  // pages picked in the thumbnails
    this.outlineList = el("ul", { class: "rd-outline" });
    this.pageBar = el("div", { class: "rd-pagebar" });
    this.thumbsPanel = el("div", { class: "rd-panel", role: "tabpanel" }, this.pageBar, this.thumbsList);
    this.outlinePanel = el("div", { class: "rd-panel", role: "tabpanel", hidden: true }, this.outlineList);
    this._buildLayers();  // (the Layers tab: shown for a document with layers, see layers.js)
    this.fieldsTab = el("button", { class: "rd-tab rd-fieldstab", type: "button", role: "tab", "aria-selected": "false", hidden: true }, "Fields");
    this.fieldsPanel = el("div", { class: "rd-panel rd-fieldspanel", role: "tabpanel", hidden: true });
    this.sidebar = el("aside", { class: "rd-sidebar", hidden: true, "aria-label": "Pages, bookmarks, comments, layers and fields" },
      el("div", { class: "rd-tabs", role: "tablist" }, this.thumbsTab, this.outlineTab, this.commentsTab, this.layersTab, this.fieldsTab),
      this.thumbsPanel, this.outlinePanel, this.commentsPanel, this.layersPanel, this.fieldsPanel);

    // Viewer
    this.viewerEl = el("div", { class: "pdfViewer" });
    this.container = el("div", { class: "rd-container", tabindex: "0" }, this.viewerEl);
    this.loading = el("div", { class: "rd-loading", hidden: true }, el("div", { class: "rd-spinner", role: "progressbar", "aria-label": "Loading" }));
    this.fileInput = el("input", { type: "file", accept: ".pdf,application/pdf,.epub,.cbz,.cbr,.dxf,.dwg,.png,.jpg,.jpeg,.webp", multiple: true, hidden: true });
    this.emptyOpenBtn = el("button", { class: "rd-btn primary", type: "button", html: svg("open") + "Open a PDF" });
    this.empty = el("div", { class: "rd-empty" },
      el("div", { class: "rd-empty-card" },
        el("div", { class: "rd-empty-icon", html: svg("book") }),
        el("h2", {}, "Read a PDF"),
        el("p", {}, "Drop a PDF here, or open one from your computer."),
        this.emptyOpenBtn,
        // (Each key with its word stays on one line; the lines come out even.)
        el("p", { class: "keys" }, ...["Ctrl+K commands", "T file panel", "E edit text", "M measure", "Ctrl+F find", "N / P pages"]
          .flatMap((keys, i) => [i ? " · " : "", el("span", {}, keys)]))),
      this.recentBox = el("section", { class: "rd-recent", hidden: true, "aria-label": "Recent files" },
        el("h3", {}, "Recent"), this.recentList = el("div", {})));
    // A file that can't be shown (damaged, or not a PDF): a state that stays
    // for its tab, with a way out (Repair), not a toast that goes.
    this.brokenName = el("h2", { class: "rd-broken-name" });
    this.brokenRepair = el("button", { class: "rd-btn primary seal rd-broken-repair", type: "button", html: svg("wrench") + "<span>Try to repair it</span>" });
    this.brokenOpenId = el("button", { class: "rd-btn primary seal rd-broken-openid", type: "button", hidden: true, html: svg("badge") + "<span>Open with my Digital ID</span>" });
    this.brokenWhy = el("p", { class: "rd-broken-why" }, BROKEN_WHY);
    this.brokenForget = el("button", { class: "rd-btn rd-broken-forget", type: "button" }, "Remove from Recent");
    this.brokenClose = el("button", { class: "rd-btn rd-broken-close", type: "button" }, "Close");
    this.brokenSaid = el("p", { class: "rd-broken-said", role: "status" });
    this.broken = el("div", { class: "rd-empty rd-broken", hidden: true },
      el("section", { class: "rd-empty-card rd-broken-card", "aria-labelledby": "rd-broken-name" },
        (this.brokenIcon = el("div", { class: "rd-empty-icon rd-broken-icon", html: svg("alert") })),
        Object.assign(this.brokenName, { id: "rd-broken-name" }),
        this.brokenWhy,
        el("div", { class: "rd-broken-actions" }, this.brokenOpenId, this.brokenRepair, this.brokenForget, this.brokenClose),
        this.brokenSaid));
    this.main = el("div", { class: "rd-main" }, this.container, this.loading, this.empty, this.broken, this.measPanel);
    this._buildSplitView();
    this._buildCropMode();
    // The file panel: beside the pages in a wide window, over them (with a
    // scrim) in a narrow one. The app puts its content in.
    this.side = el("aside", { class: "rd-side", id: "rd-side", role: "region", "aria-label": "This file", hidden: true });
    this.sideScrim = el("div", { class: "rd-side-scrim", hidden: true });
    this.body = el("div", { class: "rd-body" }, this.sidebar, this.main, this.splitDivider, this.splitPane, this.sideScrim, this.side);

    this.menu = el("div", { class: "rd-menu", role: "menu", hidden: true });
    this.ctxMenu = el("div", { class: "rd-menu rd-ctxmenu", role: "menu", hidden: true, "aria-label": "Page" });
    this.tabbar = el("div", { class: "rd-tabbar", role: "tablist", "aria-label": "Open documents", hidden: true });
    // Comparing with another revision of the drawing
    this.overlayName = el("b", {});
    this.overlayMode = el("select", { class: "rd-zoom", "aria-label": "Show" },
      el("option", { value: "both" }, "Both, changes in colour"), el("option", { value: "changes" }, "Changes only"),
      el("option", { value: "this" }, "This version only"), el("option", { value: "other" }, "The other version only"));
    this.overlayOffset = el("input", { type: "number", class: "rd-overlay-offset", value: "0", step: "1", "aria-label": "Page offset",
      title: "When the other version has its sheets in other places: its page = this page plus this number" });
    // Signatures in the document: how they check out
    this.sigText = el("span", { class: "rd-sigstatus" });
    const sigMore = el("button", { class: "rd-btn", type: "button", html: "<span>Details</span>" });
    const sigClose = el("button", { class: "rd-btn", type: "button", title: "Hide", "aria-label": "Hide", html: svg("x") });
    this.sigbar = el("div", { class: "rd-findbar rd-sigbar", hidden: true, role: "status" }, el("span", { class: "rd-sigicon", html: svg("badge") }),
      this.sigText, sigMore, el("span", { style: "flex:1" }), sigClose);
    sigMore.addEventListener("click", () => this._signatureDetails());
    sigClose.addEventListener("click", () => { this.sigbar.hidden = true; });
    // A printed form with nothing to type in: offer to add its fields.
    const formMake = el("button", { class: "rd-btn primary", type: "button", html: svg("fillable") + "<span>Make it fillable</span>" });
    const formPrep = el("button", { class: "rd-btn", type: "button", html: svg("fillable") + "<span>Prepare form</span>" });
    const formClose = el("button", { class: "rd-btn", type: "button", title: "Hide", "aria-label": "Hide", html: svg("x") });
    this.formText = el("span", { class: "rd-sigstatus" });
    this.formbar = el("div", { class: "rd-findbar rd-formbar", hidden: true, role: "status" }, el("span", { class: "rd-sigicon", html: svg("fillable") }),
      this.formText, formMake, formPrep, el("span", { style: "flex:1" }), formClose);
    formMake.addEventListener("click", () => this.makeFillable());
    formPrep.addEventListener("click", () => this.openFormMode());
    formClose.addEventListener("click", () => {
      this.formbar.hidden = true;
      if (this.activeTab) { this.activeTab.formHint = "hidden"; this.onFormHint?.(this.activeTab); }
    });
    const overlayClose = el("button", { class: "rd-btn", type: "button", title: "Stop comparing", "aria-label": "Stop comparing", html: svg("x") });
    this.overlaybar = el("div", { class: "rd-findbar rd-overlaybar", hidden: true, role: "toolbar", "aria-label": "Compare revisions" },
      el("span", { class: "rd-overlay-title" }, "Comparing with ", this.overlayName),
      el("span", { class: "rd-legend" }, el("i", { class: "other" }), "only in that one"),
      el("span", { class: "rd-legend" }, el("i", { class: "this" }), "only in this one"),
      el("span", { class: "rd-sep" }), el("label", {}, "Show", this.overlayMode),
      el("label", {}, "Its page = this page +", this.overlayOffset),
      el("span", { style: "flex:1" }), overlayClose);
    this.overlayMode.addEventListener("change", () => { if (this.overlay) { this.overlay.mode = this.overlayMode.value; this._drawOverlays(); } });
    this.overlayOffset.addEventListener("change", () => {
      if (!this.overlay) return;
      this.overlay.offset = Math.round(Number(this.overlayOffset.value) || 0);
      this._drawOverlays();
    });
    overlayClose.addEventListener("click", () => this.closeCompare());
    // Skip link: past the bars, to the pages (out of sight until it has focus).
    this.skipLink = el("a", { class: "skip-link rd-skip", href: "#", onclick: (e) => { e.preventDefault(); this.container.focus(); } }, "Skip to the page");
    this.root.append(this.skipLink, toolbar, this.tabbar, this.findbar, this.annotbar, this.formmodebar, this.checkocrbar, this.scalebar, this.overlaybar, this.splitBar, this.cropbar, this.sigbar, this.formbar, this.body, this.menu, this.ctxMenu, this.zoomMenu, this.moreToolsMenu, this.fileInput);
    // A drawing in progress takes every click in the window as the next
    // stroke; end it first when the tabs, toolbar or menu are used. This
    // listens on the window before pdf.js does, so it runs first.
    const outside = [this.tabbar, toolbar, this.side, this.scalebar, this.formmodebar, this.checkocrbar, this.splitBar, this.cropbar, this.menu, this.ctxMenu];
    window.addEventListener("pointerdown", (e) => {
      if (!outside.some((area) => area.contains(e.target))) return;
      this._finishEditing();
    }, { capture: true });
    this.host.append(this.root);
    this.#wire();
    this.#wireAriaNav();
  }

  /** Shared keyboard navigation across all Reader role="menu" popups and
   *  role="tablist" strips (sidebar tabs + open-document tabbar). */
  #wireAriaNav() {
    this.root.addEventListener("keydown", (e) => {
      const menu = e.target.closest('[role="menu"]');
      if (menu && !menu.hidden) {
        const items = [...menu.querySelectorAll('[role="menuitem"], [role="menuitemcheckbox"], [role="menuitemradio"], button')]
          .filter((b) => !b.disabled && !b.hidden && b.offsetParent !== null);
        if (!items.length) return;
        const idx = items.indexOf(e.target.closest("button"));
        if (e.key === "ArrowDown") {
          e.preventDefault();
          items[(idx + 1 + items.length) % items.length].focus();
        } else if (e.key === "ArrowUp") {
          e.preventDefault();
          items[(idx - 1 + items.length) % items.length].focus();
        } else if (e.key === "Home") {
          e.preventDefault();
          items[0].focus();
        } else if (e.key === "End") {
          e.preventDefault();
          items[items.length - 1].focus();
        } else if (e.key === "Escape") {
          e.preventDefault();
          this.#closeMenu();
        }
        return;
      }
      const tab = e.target.closest('[role="tab"]');
      const tablist = tab && tab.closest('[role="tablist"]');
      if (tab && tablist) {
        const tabs = [...tablist.querySelectorAll('[role="tab"]')]
          .filter((t) => !t.disabled && !t.hidden && t.offsetParent !== null);
        if (tabs.length < 2) return;
        const idx = tabs.indexOf(tab);
        let next = -1;
        if (e.key === "ArrowRight" || e.key === "ArrowDown") next = (idx + 1) % tabs.length;
        else if (e.key === "ArrowLeft" || e.key === "ArrowUp") next = (idx - 1 + tabs.length) % tabs.length;
        else if (e.key === "Home") next = 0;
        else if (e.key === "End") next = tabs.length - 1;
        if (next >= 0) {
          e.preventDefault();
          tabs[next].focus();
          if (tablist.classList.contains("rd-tabs")) tabs[next].click();
        }
      }
    });
  }

  #menuItem(icon, label, onclick, checkable = null) {
    const attrs = { type: "button", role: checkable === null ? "menuitem" : "menuitemcheckbox" };
    if (checkable !== null) attrs["aria-checked"] = String(!!checkable);
    const btn = el("button", attrs);
    btn.innerHTML = svg(icon);
    btn.append(el("span", {}, label));
    if (checkable !== null) btn.append(el("span", { class: "check", html: svg("check") }));
    btn.addEventListener("click", () => { this.#closeMenu(); onclick(); });
    return btn;
  }

  #renderMenu() {
    const has = !!this.doc;
    const spread = this.pdfViewer && this.pdfViewer.spreadMode !== viewer.SpreadMode.NONE;
    // (Open and the commands work without a document too.)
    const pageTheme = this.pageTheme();
    const always = [
      this.#menuItem("open", "Open a PDF…", () => this.chooseFile()),
      this.#menuItem("layers", "Workspace sessions…", () => this.openWorkspaceSessions()),
      ...(this.onCommands ? [this.#menuItem("command", "Search commands and tools…", () => this.onCommands())] : []),
    ];
    const items = [
      ...always,
      el("hr"),
      this.#menuItem("cw", "Rotate the view clockwise", () => this.rotate(90)),
      this.#menuItem("ccw", "Rotate the view counter-clockwise", () => this.rotate(-90)),
      this.#menuItem("spread", "Two-page view", () => this.toggleSpread(), spread),
      this.#menuItem("spread", "Cover page alone (align spreads)", () => this.toggleCoverOffset(), !!this.coverOffset),
      this.#menuItem("book", "Right-to-left manga reading", () => this.toggleRtlReading(), !!this.rtlReading),
      this.#menuItem("spread", "Split view (side by side)", () => this.toggleSplitView(), this.splitOpen),
      this.#menuItem("hand", "Hand tool (drag to scroll)", () => this.toggleHand(), this.container.classList.contains("hand")),
      this.#menuItem("moon", "Page theme: Smart Night", () => this.setPageTheme(pageTheme === "night" ? "light" : "night"), pageTheme === "night"),
      this.#menuItem("book", "Page theme: Warm Sepia", () => this.setPageTheme(pageTheme === "sepia" ? "light" : "sepia"), pageTheme === "sepia"),
      this.#menuItem("screen", "Page theme: E-Ink High Contrast", () => this.setPageTheme(pageTheme === "eink" ? "light" : "eink"), pageTheme === "eink"),
      this.#menuItem("link", "Ask before opening web links", () => store.set("pdfm-link-ask", !store.get("pdfm-link-ask", true)), store.get("pdfm-link-ask", true)),
      el("hr"),
      this.#menuItem("full", "Full screen", () => this.toggleFullscreen()),
      this.#menuItem("print", "Print…", () => this.print()),
      ...(this.activeTab?.fileId ? [this.#menuItem("save", "Save", () => this.save())] : []),
      this.#menuItem("save", this.activeTab?.fileId ? "Save as…" : this._edited() ? "Save a copy with your changes…" : "Save a copy…", () => this.saveCopy()),
      this.#menuItem("layers", this.overlay ? "Stop comparing revisions" : "Compare with another revision…", () => (this.overlay ? this.closeCompare() : this.compareRevision())),
      this.#menuItem("badge", "Digital ID…", () => this.useTool("CERTSIGN")),
      this.#menuItem("fillable", "Prepare form (add or edit form fields)", () => this.openFormMode()),
      this.#menuItem("fillable", "Make it fillable (find the form's fields)", () => this.makeFillable()),
      this.#menuItem("edittext", "Check & Edit OCR text layer…", () => this.openCheckOcr()),
      this.#menuItem("rect", "Crop pages visually…", () => this.openCropMode(), this.cropMode),
      this.#menuItem("print", "Print preflight check…", () => this.checkPreflight()),
      this.#menuItem("edittext", "Translate this page…", () => this.translateCurrentPage()),
      this.#menuItem("edittext", "Manga Lens (live page overlay & bubble editor)", () => this.openMangaLens(), !!this.mangaLens?.active),
      this.#menuItem("split", "Live bilingual reading…", () => this.openSplitView("__translate__")),
      this.#menuItem("command", "Local AI assistant (Ask / Summarize / Extract)…", () => openAiAssistant(this, "ask")),
      this.#menuItem("info", "Document properties", () => this.showProperties()),
      this.#menuItem("access", "Check accessibility", () => this.checkAccessibility()),  // -- accessibility --
      this.#menuItem("x", "Close this document", () => this.closeTab()),
    ];
    if (this.onEdit && this.tools.length) {
      items.push(el("hr"), el("div", { class: "label" }, "Edit with"));
      for (const [id, label] of this.tools) items.push(this.#menuItem("tool", label, () => this.#edit(id)));
    }
    for (const item of items) if (item.tagName === "BUTTON" && !always.includes(item)) item.disabled = !has;
    this.menu.replaceChildren(...items);
  }

  #toggleMenu() {
    if (!this.menu.hidden) return this.#closeMenu();
    this.#renderMenu();
    // Open under the More button.
    const at = this.moreBtn.getBoundingClientRect(), box = this.root.getBoundingClientRect();
    this.menu.style.top = `${at.bottom - box.top + 6}px`;
    this.menu.hidden = false;
    this.moreBtn.setAttribute("aria-expanded", "true");
    const first = this.menu.querySelector("button:not(:disabled)");
    if (first) first.focus();
  }
  #closeMenu() { this.menu.hidden = true; this.moreBtn.setAttribute("aria-expanded", "false"); }

  // ---- zoom -------------------------------------------------------------------
  /** Show the zoom in its box: the percentage, and which preset it is. */
  _showZoom(scale = this.pdfViewer?.currentScale) {
    if (!this.doc || !Number.isFinite(scale)) return;
    this.zoomInput.value = Math.round(scale * 100) + "%";
    const preset = ZOOM_PRESETS.find(([v]) => v === this.zoomPreset);
    this.zoomMenuBtn.title = preset ? `Zoom presets (now: ${preset[1]})` : "Zoom presets";
  }

  /** Zoom to ``factor`` (1 = 100%), kept to what the viewer can draw. */
  setZoom(factor) {
    if (!this.doc || !Number.isFinite(factor)) return false;
    const zoom = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, factor));
    this.pdfViewer.currentScale = zoom;
    if (zoom !== factor) this.toast(`Zoom goes from ${MIN_ZOOM * 100}% to ${MAX_ZOOM * 100}%.`, 2500);
    return true;
  }

  /** The zoom typed into the box: 137, 137%, 137.5 (or 1.37, as a factor). */
  #applyTypedZoom() {
    const text = this.zoomInput.value.trim().replace(",", ".");
    const m = text.match(/^(\d+(?:\.\d+)?)\s*%?$/);
    const n = m ? parseFloat(m[1]) : NaN;
    if (!(n > 0)) { this._showZoom(); if (text) this.toast("Type a zoom such as 150 or 66.7%."); return; }
    this.setZoom(n < 10 && !text.includes("%") ? n : n / 100);
    this._showZoom();
  }

  #toggleZoomMenu() {
    if (!this.zoomMenu.hidden) return this.#closeZoomMenu();
    if (!this.doc) return;
    const items = [];
    for (const [value, label] of ZOOM_PRESETS) {
      if (value === "0.5") items.push(el("hr"));
      const item = el("button", { type: "button", role: "menuitemradio", "aria-checked": String(value === this.zoomPreset) },
        el("span", {}, label), el("span", { class: "check", html: svg("check") }));
      item.addEventListener("click", () => {
        this.#closeZoomMenu();
        if (value === "true-size") this._viewAt("true");
        else this.pdfViewer.currentScaleValue = value;
        this.container.focus();
      });
      items.push(item);
    }
    this.zoomMenu.replaceChildren(...items);
    const at = this.zoomInput.getBoundingClientRect(), box = this.root.getBoundingClientRect();
    this.zoomMenu.style.top = `${at.bottom - box.top + 6}px`;
    this.zoomMenu.style.left = `${Math.max(8, at.left - box.left)}px`;
    this.zoomMenu.style.right = "auto";
    this.zoomMenu.hidden = false;
    this.zoomMenuBtn.setAttribute("aria-expanded", "true");
    (this.zoomMenu.querySelector("[aria-checked=true]") || this.zoomMenu.querySelector("button")).focus();
  }
  #closeZoomMenu() { this.zoomMenu.hidden = true; this.zoomMenuBtn.setAttribute("aria-expanded", "false"); }

  // ---- events -----------------------------------------------------------------
  #wire() {
    this.emptyOpenBtn.addEventListener("click", () => this.chooseFile());
    this.brokenRepair.addEventListener("click", () => this._repairBroken());
    this.brokenOpenId.addEventListener("click", () => this._openBrokenWithId());
    this.brokenForget.addEventListener("click", () => this._forgetBroken());
    this.brokenClose.addEventListener("click", () => this.closeTab(this.activeTab));
    this.fileInput.addEventListener("change", () => { const f = this.fileInput.files[0]; this.fileInput.value = ""; if (f) this.openFile(f); });
    this.sidebarBtn.addEventListener("click", () => this.toggleSidebar());
    this.prevBtn.addEventListener("click", () => this.pdfViewer.previousPage());
    this.nextBtn.addEventListener("click", () => this.pdfViewer.nextPage());
    this.pageInput.addEventListener("change", () => this._goToTypedPage());
    this.pageInput.addEventListener("keydown", (e) => { if (e.key === "Enter") { this._goToTypedPage(); this.container.focus(); } });
    this.pageInput.addEventListener("focus", () => this.pageInput.select());
    this.zoomInBtn.addEventListener("click", () => this.pdfViewer.increaseScale());
    this.zoomOutBtn.addEventListener("click", () => this.pdfViewer.decreaseScale());
    this.zoomInput.addEventListener("focus", () => this.zoomInput.select());
    this.zoomInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); this.#applyTypedZoom(); this.container.focus(); }
      else if (e.key === "Escape") { e.preventDefault(); this._showZoom(); this.container.focus(); }
      else if (e.key === "ArrowUp" || e.key === "ArrowDown") {
        e.preventDefault();
        const step = (e.shiftKey ? 1 : 10) * (e.key === "ArrowUp" ? 1 : -1);
        this.setZoom((Math.round(this.pdfViewer.currentScale * 100) + step) / 100);
        this.zoomInput.select();
      }
    });
    this.zoomInput.addEventListener("change", () => this.#applyTypedZoom());
    this.zoomMenuBtn.addEventListener("click", () => this.#toggleZoomMenu());
    document.addEventListener("pointerdown", (e) => { if (!this.zoomMenu.hidden && !this.zoomMenu.contains(e.target) && !this.zoomMenuBtn.contains(e.target)) this.#closeZoomMenu(); });
    this.scaleBtn.addEventListener("click", () => this.toggleScaleBar());
    this.findBtn.addEventListener("click", () => (this.findbar.hidden ? this.openFind() : this.closeFind()));
    this.moreBtn.addEventListener("click", () => this.#toggleMenu());
    this.panelBtn.addEventListener("click", () => this.togglePanel());
    this.sideScrim.addEventListener("click", () => this.setPanel(false, { focus: true }));
    // A narrow window shows the panel over the pages; it closes on Escape.
    this.side.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && !this.#panelDocked() && !document.querySelector("dialog[open]")) { e.preventDefault(); e.stopPropagation(); this.setPanel(false, { focus: true }); }
    });
    this.wideQuery = matchMedia("(min-width: 1100px)");
    this.wideQuery.addEventListener("change", () => this._applyPanel());
    this.saveNowBtn.addEventListener("click", () => this.save());
    this.undoNowBtn.addEventListener("click", () => this._undo());
    this.homeBtn?.addEventListener("click", () => this.onHome());
    document.addEventListener("pointerdown", (e) => { if (!this.menu.hidden && !this.menu.contains(e.target) && e.target !== this.moreBtn && !this.moreBtn.contains(e.target)) this.#closeMenu(); });
    this.thumbsTab.addEventListener("click", () => this._showTab("thumbs"));
    this.outlineTab.addEventListener("click", () => this._showTab("outline"));
    this.commentsTab.addEventListener("click", () => this._showTab("comments"));
    this.fieldsTab.addEventListener("click", () => this._showTab("fields"));
    this.authorInput.addEventListener("change", () => store.set("pdfm-author", this.authorInput.value.trim()));
    this.shapeSelect.addEventListener("change", () => { store.set("pdfm-shape", this.shapeSelect.value); this._shapeChosen(); });
    this.stampSelect.addEventListener("change", () => this._stampChosen());
    this._loadStamps();

    this._wireFind();

    // Annotating
    this.annotateBtn.addEventListener("click", () => (this.annotbar.hidden ? this.openAnnotate() : this.closeAnnotate()));
    for (const btn of this.annotTools) btn.addEventListener("click", () => {
      const fromMenu = !this.moreToolsMenu.hidden && this.moreToolsMenu.contains(btn);
      if (fromMenu) this._closeToolsMenu(true);
      this._setEditMode(btn.dataset.mode, btn.dataset.mode === "STAMP");
    });
    this.signBtn.addEventListener("click", () => this.sign());
    this.moreToolsBtn.addEventListener("click", () => this._toggleToolsMenu());
    this.moreToolsBtn.addEventListener("keydown", (e) => {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); if (this.moreToolsMenu.hidden) this._toggleToolsMenu(e.key === "ArrowUp" ? "last" : "checked"); }
    });
    // In the menu: arrows, Home and End move; Escape (or Tab) closes it, focus back on its button.
    this.moreToolsMenu.addEventListener("keydown", (e) => {
      const items = this.moreTools, at = items.indexOf(document.activeElement);
      const go = (i) => { e.preventDefault(); items[(i + items.length) % items.length].focus(); };
      if (e.key === "ArrowDown") go(at + 1);
      else if (e.key === "ArrowUp") go(at < 0 ? items.length - 1 : at - 1);
      else if (e.key === "Home") go(0);
      else if (e.key === "End") go(items.length - 1);
      else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); this._closeToolsMenu(true); }
      else if (e.key === "Tab") this._closeToolsMenu();
    });
    document.addEventListener("pointerdown", (e) => {
      if (!this.moreToolsMenu.hidden && !this.moreToolsMenu.contains(e.target) && !this.moreToolsBtn.contains(e.target)) this._closeToolsMenu();
    });
    for (const sw of this.swatches) sw.addEventListener("click", () => this._setColor(+sw.dataset.index));
    this.sizeSelect.addEventListener("change", () => this._setSize(+this.sizeSelect.value));
    this.undoBtn.addEventListener("click", () => this._undo());
    this.redoBtn.addEventListener("click", () => this._undo(true));
    this.deleteBtn.addEventListener("click", () => { if (!this._markDelete()) this.uiManager?.delete(); });
    this.annotSaveBtn.addEventListener("click", () => this.save());
    this.annotCloseBtn.addEventListener("click", () => this.closeAnnotate());

    // Drop a PDF anywhere on the reader to open it.
    const hasFiles = (e) => e.dataTransfer && [...e.dataTransfer.types].includes("Files");
    this.root.addEventListener("dragover", (e) => { if (hasFiles(e)) { e.preventDefault(); e.stopPropagation(); this.empty.classList.add("over"); } });
    this.root.addEventListener("dragleave", (e) => { if (!this.root.contains(e.relatedTarget)) this.empty.classList.remove("over"); });
    this.root.addEventListener("drop", (e) => {
      this.empty.classList.remove("over");
      if (!hasFiles(e)) return;
      e.preventDefault(); e.stopPropagation();
      const dropped = [...e.dataTransfer.files];
      const isDoc = (f) => f.type === "application/pdf" || /\.(pdf|epub|cbz|cbr|dxf|dwg)$/i.test(f.name);
      const isImg = (f) => /\.(png|jpe?g|webp|bmp|tiff?)$/i.test(f.name);
      const file = dropped.find(isDoc);
      if (file) {
        // Remember dropped files for Recent (the handle must be asked for right away).
        const item = [...e.dataTransfer.items].find((i) => i.kind === "file" && i.getAsFile() && isDoc(i.getAsFile()));
        const handle = item && item.getAsFileSystemHandle ? item.getAsFileSystemHandle() : null;
        if (handle) handle.then((h) => h && h.kind === "file" && files.rememberHandle(h)).catch(() => {});
        this.openFile(file);
        return;
      }
      const imgs = dropped.filter(isImg);
      if (imgs.length) {
        this.openImageFiles(imgs);
        return;
      }
      this.toast("That isn't a PDF, EPUB, CBZ/CBR, drawing, or image.");
    });

    // Ctrl/⌘ + wheel zooms around the pointer.
    this.container.addEventListener("wheel", (e) => {
      if (!(e.ctrlKey || e.metaKey) || !this.doc) return;
      e.preventDefault();
      const origin = [e.clientX, e.clientY];
      const factor = Math.exp(-e.deltaY / (e.deltaMode === 1 ? 20 : 300));
      this.pdfViewer.updateScale({ drawingDelay: 300, scaleFactor: Math.min(1.5, Math.max(0.67, factor)), origin });
    }, { passive: false });

    // Hand tool: drag to scroll.
    let drag = null;
    this.container.addEventListener("pointerdown", (e) => {
      if (!this.container.classList.contains("hand") || this.textEditMode || e.button !== 0 || e.target.closest("a, input, textarea, select, button")) return;
      drag = { x: e.clientX, y: e.clientY, left: this.container.scrollLeft, top: this.container.scrollTop };
      this.container.classList.add("grabbing");
      this.container.setPointerCapture(e.pointerId);
      e.preventDefault();
    });
    this.container.addEventListener("pointermove", (e) => {
      if (!drag) return;
      this.container.scrollLeft = drag.left - (e.clientX - drag.x);
      this.container.scrollTop = drag.top - (e.clientY - drag.y);
    });
    const endDrag = () => { drag = null; this.container.classList.remove("grabbing"); };
    this.container.addEventListener("pointerup", endDrag);
    this.container.addEventListener("pointercancel", endDrag);

    // Right-click a page (or its thumbnail) for what can be done with it.
    this.container.addEventListener("contextmenu", (e) => this._pageMenu(e));
    this.thumbsList.addEventListener("contextmenu", (e) => this._pageMenu(e));
    document.addEventListener("pointerdown", (e) => { if (!this.ctxMenu.hidden && !this.ctxMenu.contains(e.target)) this._closeContextMenu(); }, true);
    this.container.addEventListener("scroll", () => this._closeContextMenu(), { passive: true });
    this._wireThumbs();

    // Measuring: clicks on a page place the points.
    this.container.addEventListener("pointerdown", (e) => this._measureDown(e), true);
    this.container.addEventListener("pointermove", (e) => this._measureMove(e));
    window.addEventListener("pointerup", () => this._measureUp());

    // Edit text tool: click a line of text to change it.
    this.container.addEventListener("click", (e) => this._textClick(e), true);
    this.container.addEventListener("mousedown", (e) => this._textPress(e), true);

    // Select (no tool): a click on a signature, drawing, text box or
    // highlight picks it (pdf.js's own way is a double-click).
    // (Pressed and let go without moving: no "click" event, which the browser
    // doesn't always send when pdf.js's text layer moves things under the
    // pointer meanwhile.)
    // A link out of the document: its real address is shown first (_openLink).
    for (const type of ["click", "auxclick"]) {
      this.container.addEventListener(type, (e) => {
        const a = e.target.closest?.(".annotationLayer a[href]");
        const href = a?.getAttribute("href");
        if (!href || href.startsWith("#") || (type === "auxclick" && e.button !== 1)) return;
        e.preventDefault();
        e.stopPropagation();
        this._openLink(a.href);
      }, true);
    }
    let pressed = null;
    this.container.addEventListener("pointerdown", (e) => { pressed = e.isPrimary ? [e.clientX, e.clientY, e.timeStamp] : null; }, true);
    this.container.addEventListener("pointerup", (e) => {
      const still = pressed && Math.hypot(e.clientX - pressed[0], e.clientY - pressed[1]) < 5 && e.timeStamp - pressed[2] < 800;
      pressed = null;
      if (still) this._pickOnClick(e);
    });

    // Notes and shapes: click or drag on a page; underline and strike out: select words.
    this.container.addEventListener("pointerdown", (e) => this._markupDown(e), true);
    this.container.addEventListener("pointermove", (e) => this._markupMove(e));
    window.addEventListener("pointerup", (e) => this._markupUp(e));

    document.addEventListener("keydown", (e) => this.#onKey(e));
    document.addEventListener("keyup", (e) => this._markKeyUp(e));
  }

  #onKey(e) {
    if (!this.active || document.querySelector("dialog[open]")) return;
    const mod = e.ctrlKey || e.metaKey;
    if (mod && e.key.toLowerCase() === "o") { e.preventDefault(); this.chooseFile(); return; }
    if (e.key === "Escape" && !this.menu.hidden) { this.#closeMenu(); this.moreBtn.focus(); return; }
    if (e.key === "Escape" && !this.zoomMenu.hidden) { this.#closeZoomMenu(); this.zoomMenuBtn.focus(); return; }
    if (!this.doc) {
      // (The document is being shown again after a markup changed: its keys are still its own.)
      if (!mod && !e.altKey && !isTyping(e) && this.markPending && this._markKey(e)) e.preventDefault();
      return;
    }
    if (e.key === "Escape" && this.panelOpen && !this.#panelDocked()) { e.preventDefault(); this.setPanel(false, { focus: true }); return; }
    if (mod && e.key.toLowerCase() === "f") { e.preventDefault(); this.openFind(); return; }
    if (mod && e.key.toLowerCase() === "p") { e.preventDefault(); this.print(); return; }
    if (mod && e.key.toLowerCase() === "s") { e.preventDefault(); this._saveKey(e); return; }
    if (mod && !isTyping(e) && !this.scalebar.hidden && this.annotbar.hidden && (e.key.toLowerCase() === "z" || e.key.toLowerCase() === "y")
        && this._measureUndo(e.key.toLowerCase() === "y" || e.shiftKey)) { e.preventDefault(); return; }
    // Ctrl+Z / Ctrl+Y (Ctrl+Shift+Z): what the Undo and Redo buttons do, with
    // any tool on or none (with Select, pdf.js has no keys of its own; with
    // its tools on, it doesn't hear them as well: stopPropagation).
    if (mod && !e.altKey && (e.key.toLowerCase() === "z" || e.key.toLowerCase() === "y") && !isTyping(e)) {
      this._formFlushNudge();
      if (this._undo(e.key.toLowerCase() === "y" || e.shiftKey)) { e.preventDefault(); e.stopPropagation(); return; }
    }
    if (this.formMode && this._formKeyDown(e)) return;
    if (e.key === "Escape" && !this.ctxMenu.hidden) { this._closeContextMenu(); return; }
    if (mod && !e.altKey && (e.key === "\\" || e.code === "Backslash")) {
      e.preventDefault();
      if (e.shiftKey) this.switchSplitPane();
      else this.toggleSplitView();
      return;
    }
    if (e.key === "F6" && !mod && !e.altKey && this.splitOpen) {
      e.preventDefault();
      this.switchSplitPane();
      return;
    }
    if (mod && (e.key === "=" || e.key === "+")) { e.preventDefault(); this.pdfViewer.increaseScale(); return; }
    if (mod && e.key === "-") { e.preventDefault(); this.pdfViewer.decreaseScale(); return; }
    if (mod && e.key === "0") { e.preventDefault(); this.pdfViewer.currentScaleValue = "auto"; return; }
    if (this.cropMode && !mod && !isTyping(e) && this._cropKey?.(e)) { e.preventDefault(); return; }
    if (mod || e.altKey || isTyping(e)) return;
    if (this.measureMode && this._measureKey(e)) { e.preventDefault(); return; }
    if (this.markupMode === "REDACT" && this._redactKey?.(e)) { e.preventDefault(); return; }
    // (Not a key another part already took, or one pressed in a side panel: Delete in the page list removes pages.)
    if (!e.defaultPrevented && !this.side?.contains(e.target) && !this.sidebar?.contains(e.target) && this._markHasKeys() && this._markKey(e)) { e.preventDefault(); return; }   // a selected markup: Delete, Esc, the arrows
    if (e.key === "Escape" && !this.findbar.hidden) { this.closeFind(); return; }
    if (this.markupMode === "IMAGES" && this.pickedImage && !isTyping(e) && (e.key === "Delete" || e.key === "Backspace")) {
      e.preventDefault();
      this._editImage("delete");
      return;
    }
    if (e.key === "Escape" && this.markupMode === "IMAGES" && this.pickedImage) { this._pickImage(null); return; }
    if (e.key === "Escape" && this.editMode && this.editMode !== "NONE") { this._setEditMode("NONE"); return; }
    if (e.key === "Escape" && this.cropMode) { this.closeCropMode(); return; }
    if (e.key === "Escape" && this.checkOcrMode) {
      if (this._ocrPop) this._closeCheckOcrPop();
      else this.closeCheckOcr();
      return;
    }
    // With no tool left on, Escape closes the annotate bar (focus back on its button).
    if (e.key === "Escape" && !this.annotbar.hidden) { this.closeAnnotate(); if (this.findbar.hidden) this.annotateBtn.focus(); return; }
    const v = this.pdfViewer;
    switch (e.key) {
      case "n": case "j": v.nextPage(); break;
      case "p": case "k": v.previousPage(); break;
      case "r": this.rotate(90); break;
      case "R": this.rotate(-90); break;
      case "t": this.togglePanel(); break;
      case "T": this.openMangaLens(); break;
      case "e": this.useTool(this.textEditMode ? "NONE" : "EDITTEXT"); break;
      case "m": this.measureTool(this.measureMode === "distance" ? null : "distance"); break;
      // The annotate bar's labelled tools, while it's open (H, C, A, D, S; again: back to Select).
      case "h": case "c": case "a": case "d": case "s": {
        if (this.annotbar.hidden || e.shiftKey) return;
        const mode = ANNOT_KEYS[e.key];
        if (mode === "SIGN") this.sign();
        else this._setEditMode(this.editMode === mode ? "NONE" : mode);
        break;
      }
      case "Home": v.currentPageNumber = 1; break;
      case "End": v.currentPageNumber = v.pagesCount; break;
      case "ArrowLeft":
        if (this.container.scrollWidth <= this.container.clientWidth) {
          if (this.rtlReading) v.nextPage(); else v.previousPage();
        } else return;
        break;
      case "ArrowRight":
        if (this.container.scrollWidth <= this.container.clientWidth) {
          if (this.rtlReading) v.previousPage(); else v.nextPage();
        } else return;
        break;
      default: return;
    }
    e.preventDefault();
  }

  // ---- visibility (called by the app when switching tools) --------------------
  show() {
    this.active = true; this.root.hidden = false;
    if (this.doc) this.container.focus({ preventScroll: true });
    // (A moment later: when the reader is shown to open a file in it, the
    // Recent list isn't needed, and listing it would keep the server busy
    // while the file is being read.)
    else setTimeout(() => { if (!this.doc && !this.opening) this.refreshRecent(); }, 0);
  }
  hide() { this.active = false; this.root.hidden = true; this.#closeMenu(); }


  _formScriptsOnly(doc) { return formScriptsOnly(doc); }

  // ---- the file panel (its content comes from the app) ----------------------------
  /** In a window 1100 px wide or more the panel sits beside the pages and
   *  stays open or closed as it was left ("pdfm-file-panel"); narrower, it
   *  comes over the pages when asked for and goes again. */
  #panelDocked() { return !this.wideQuery || this.wideQuery.matches; }
  get panelOpen() { return this.#panelDocked() ? store.get("pdfm-file-panel", true) !== false : !!this.overlayOpen; }
  /** While the annotate bar or the find bar is open the file panel steps
   *  aside (to its button), so the page has the room; it comes back when
   *  they close. Its open/closed choice isn't changed. Opened again by hand
   *  meanwhile, it stays. */
  _stepAside() {
    const busy = !this.annotbar.hidden || !this.findbar.hidden;
    if (!busy) this.panelKept = false;
    const aside = busy && !this.panelKept;
    if (aside === !!this.panelAside) return;
    const had = aside && this.side.contains(document.activeElement);
    this.panelAside = aside;
    this._applyPanel();
    if (had) this.container.focus({ preventScroll: true });
  }

  _applyPanel() {
    const docked = this.#panelDocked(), open = this.panelOpen && !this.panelAside;
    this.side.hidden = !open;
    this.side.classList.toggle("overlay", !docked);
    this.sideScrim.hidden = !(open && !docked);
    this.panelBtn.setAttribute("aria-expanded", String(open));
  }
  /** Open or close the panel; ``focus``: move the focus into it (opening) or
   *  back to the page (closing). */
  setPanel(open, { focus = false } = {}) {
    const had = this.side.contains(document.activeElement);
    if (open && this.panelAside) { this.panelAside = false; this.panelKept = true; }  // asked for by hand: it stays
    else if (!open && this.panelAside) { this._applyPanel(); return; }  // (already out of the way)
    if (this.#panelDocked()) store.set("pdfm-file-panel", !!open);
    else this.overlayOpen = !!open;
    this._applyPanel();
    if (open && focus) (this.side.querySelector("button, [href], input, select, [tabindex]:not([tabindex='-1'])") || this.side).focus?.();
    if (!open && (focus || had)) (this.doc ? this.panelBtn : this.container).focus({ preventScroll: true });
  }
  togglePanel() { this.setPanel(!(this.panelOpen && !this.panelAside), { focus: !this.#panelDocked() }); }


  // ---- dialogs ----------------------------------------------------------------
  _dialog(title, body, buttons, { ready = null } = {}) {
    return new Promise((resolve) => {
      // Enter in a box of the dialog presses the form's first submit button:
      // that has to be the main one ("Open", not the "Cancel" before it), so
      // where there is a main button the others close the dialog themselves.
      const main = buttons.some(([, , primary]) => primary);
      const form = el("form", { method: "dialog" }, el("h3", {}, title), ...body,
        el("div", { class: "actions" }, ...buttons.map(([value, label, primary]) => (primary || !main
          ? el("button", { value, class: primary ? (primary === "seal" ? "primary seal" : "primary") : null, type: "submit" }, label)
          : el("button", { value, type: "button", onclick: () => dialog.close(value) }, label)))));
      const dialog = el("dialog", { class: "rd-dialog" }, form);
      // ``ready``: whether the main button can do its work yet; if not (Enter
      // in a box before everything needed is given) the dialog stays open.
      if (ready) form.addEventListener("submit", (e) => { if (!ready()) e.preventDefault(); });
      const before = document.activeElement;
      dialog.addEventListener("close", () => {
        resolve(dialog.returnValue);
        dialog.remove();
        // Focus back where it was; opened with nothing focused (a file opening): the page view, or Open a PDF.
        setTimeout(() => {
          if (document.activeElement && document.activeElement !== document.body) return;
          if (document.querySelector("dialog[open]")) return;
          const back = before && before !== document.body && before.isConnected && before.getClientRects().length ? before
            : this.doc && this.container.getClientRects().length ? this.container
            : this.emptyOpenBtn.getClientRects().length ? this.emptyOpenBtn : this.root;
          back.focus({ preventScroll: true });
        }, 0);
      });
      dialog.addEventListener("cancel", () => { dialog.returnValue = "cancel"; });
      this.root.append(dialog);
      dialog.showModal();
    });
  }

  async _askPassword(wrong) {
    const inputId = `rd-pw-${Math.random().toString(36).slice(2)}`;
    const input = el("input", { type: "password", id: inputId, autocomplete: "off", spellcheck: "false" });
    const toggle = el("button", { type: "button", class: "pw-toggle", "aria-pressed": "false", "aria-label": "Show password" }, "Show");
    toggle.addEventListener("click", () => {
      const show = input.type === "password";
      input.type = show ? "text" : "password";
      toggle.setAttribute("aria-pressed", String(show));
      toggle.textContent = show ? "Hide" : "Show";
      input.focus();
    });
    const body = [el("p", {}, `“${this.name}” is protected. Enter its password to open it.`),
      // The label names the input only; the Show button sits beside it, outside the label.
      el("div", { class: "rd-field rd-pwfield" }, el("label", { for: inputId }, "Password"), el("span", { class: "rd-pwrow" }, input, toggle))];
    if (wrong) {
      const id = `rd-pw-err-${Math.random().toString(36).slice(2)}`;
      body.push(el("div", { class: "err", role: "alert", id }, "That password is incorrect."));
      input.setAttribute("aria-invalid", "true");
      input.setAttribute("aria-describedby", id);
    }
    const pending = this._dialog("Password required", body, [["cancel", "Cancel"], ["ok", "Open", true]]);
    input.focus();
    const answer = await pending;
    return answer === "ok" ? input.value : null;
  }

  async showProperties() {
    const doc = this.doc;
    if (!doc) return;
    let info, metadata, page;
    try {
      ({ info, metadata } = await doc.getMetadata());
      page = await doc.getPage(this.pdfViewer.currentPageNumber);
    } catch (e) {
      if (e.closed) return;  // another tab shown meanwhile
      throw e;
    }
    const [x1, y1, x2, y2] = page.view;
    const w = Math.abs(x2 - x1), h = Math.abs(y2 - y1);
    const date = (raw) => {
      const d = raw && pdfjs.PDFDateString.toDateObject(raw);
      return d ? d.toLocaleString() : "";
    };
    const size = (n) => n > 1048576 ? (n / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(n / 1024)) + " KB";
    // -- accessibility -- tagged: says so (MarkInfo) or has a structure tree (pdf.js gives MarkInfo as a Map)
    const mark = await doc.getMarkInfo().catch(() => null);
    const tagged = (mark instanceof Map ? mark.get("Marked") : mark?.Marked) || !!(await page.getStructTree().catch(() => null));
    // -- end of accessibility --
    const rows = [
      ["File", this.activeTab?.path || this.name], ["File size", size(this.bytes?.length ?? this.activeTab?.size ?? 0)],
      ["Title", metadata?.get("dc:title") || info.Title], ["Author", info.Author], ["Subject", info.Subject],
      ["Keywords", info.Keywords], ["Created", date(info.CreationDate)], ["Modified", date(info.ModDate)],
      ["Application", info.Creator], ["PDF producer", info.Producer], ["PDF version", info.PDFFormatVersion],
      ["Pages", String(doc.numPages)],
      ["Tagged (accessible)", tagged ? "Yes" : "No"], ["Language", info.Language || "Not set"],  // -- accessibility --
      ["Page size", `${(w / 72 * 25.4).toFixed(0)} × ${(h / 72 * 25.4).toFixed(0)} mm (${(w / 72).toFixed(2)} × ${(h / 72).toFixed(2)} in)`],
      ["Fast web view", info.IsLinearized ? "Yes" : "No"],
      ["Form", info.IsAcroFormPresent ? "Yes" : info.IsXFAPresent ? "Yes (XFA)" : "No"],
      ["Password protected", this.password ? "Yes" : "No"],
    ].filter(([, v]) => v);
    // -- limits and attached files --
    const perms = await doc.getPermissions().catch(() => null);
    if (perms) rows.push(["Limits", permissionText(perms)]);
    const attached = Object.values((await doc.getAttachments().catch(() => null)) || {});
    const dl = el("dl", { class: "rd-props" });
    for (const [k, v] of rows) dl.append(el("dt", {}, k), el("dd", {}, v));
    const parts = [dl];
    let pendingAction = null;
    const attachInput = el("input", { type: "file", multiple: "multiple", hidden: "hidden" });
    const attachHead = el("div", { style: "display:flex;align-items:center;justify-content:space-between;gap:8px;margin-top:12px;" },
      el("h3", { style: "margin:0;" }, `Attached files (${attached.length})`),
      el("button", {
        type: "button", class: "rd-btn",
        onclick: () => attachInput.click(),
      }, "Attach file…"),
      attachInput,
    );
    parts.push(attachHead);
    if (attached.length) {
      parts.push(el("ul", { class: "rd-attached" },
        ...attached.map((a) => el("li", {},
          el("span", {}, a.filename, a.description ? el("small", {}, " — " + a.description) : null),
          el("small", {}, size(a.content?.length || 0)),
          el("button", { type: "button", class: "rd-btn", onclick: () => this._saveBytes(a.content, a.filename, () => {}, this.activeTab?.fileId) }, "Save…"),
          el("button", {
            type: "button", class: "rd-btn",
            onclick: () => {
              pendingAction = { type: "remove", name: a.filename };
              this.root.querySelector("dialog.rd-dialog button[value='ok']")?.click();
            },
          }, "Remove"),
        ))));
    } else {
      parts.push(el("p", { style: "margin:6px 0 0;color:var(--muted);font-size:13px;" }, "No embedded files."));
    }
    attachInput.addEventListener("change", () => {
      if (attachInput.files?.length) {
        pendingAction = { type: "add", files: Array.from(attachInput.files) };
        this.root.querySelector("dialog.rd-dialog button[value='ok']")?.click();
      }
    });
    // -- end of limits and attached files --
    await this._dialog("Document properties", parts, [["ok", "Close", true]]);
    if (pendingAction) {
      await this._updateAttachments(pendingAction);
      await this.showProperties();
    }
  }

  async _updateAttachments(action) {
    if (!this.activeTab) return;
    await this._changeDocument(async (bytes, tab) => {
      const body = new FormData();
      body.append("file", new Blob([bytes], { type: "application/pdf" }), tab.name || "file.pdf");
      if (tab.password) body.append("password", tab.password);
      if (action.type === "remove") {
        body.append("remove", JSON.stringify([action.name]));
      } else if (action.type === "add") {
        for (const f of action.files || []) body.append("attach", f, f.name);
      }
      const res = await fetch("/api/attachments/update", {
        method: "POST",
        headers: this.token ? { "X-Pdfm-Token": this.token } : {},
        body,
      });
      if (!res.ok) {
        let msg = res.statusText;
        try { msg = (await res.json()).error || msg; } catch (_) {}
        throw new Error(msg);
      }
      return res;
    }, { failure: "Could not update attachments: " });
    this.toast(action.type === "remove" ? `Removed attachment "${action.name}".` : `Attached ${action.files.length} file${action.files.length === 1 ? "" : "s"}.`);
  }

  // -- accessibility --
  /** Check accessibility: what keeps screen readers out of this document,
   *  and how to fix it (accessibility.py), in a dialog. */
  async checkAccessibility() {
    const tab = this.activeTab;
    if (!tab || !this.doc) return;
    this.toast("Checking accessibility…", 120000);
    let result;
    try {
      const body = new FormData();
      body.append("file", new Blob([await this._fullBytes()], { type: "application/pdf" }), "file.pdf");
      body.append("password", tab.password || "");
      const res = await fetch("/api/accessibility/check", { method: "POST", body, headers: { "X-Pdfm-Token": this.token } });
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || res.statusText);
      result = await res.json();
    } catch (e) {
      this.toast("Couldn't check accessibility: " + (e.message || e));
      return;
    }
    this.toastEl?.remove();
    const pages = (list) => list.length ? ` (page${list.length > 1 ? "s" : ""} ${list.slice(0, 10).join(", ")}${list.length > 10 ? "…" : ""})` : "";
    const items = result.issues.map((i) => el("li", { class: "rd-sigitem invalid" },
      el("b", {}, i.message + pages(i.pages)), el("small", {}, "How to fix: " + i.fix)));
    const body = [el("p", {}, result.passed ? "No problems found." : `${result.issues.length} problem${result.issues.length > 1 ? "s" : ""} found.`)];
    if (items.length) body.push(el("ul", { class: "rd-siglist" }, ...items));
    body.push(el("p", { class: "rd-muted", style: "margin-top: 16px" }, result.note));
    const fix = !result.passed && this.onEdit && this.tools.some(([id]) => id === "tag");
    const answer = await this._dialog("Accessibility", body, [...(fix ? [["tag", "Make accessible…"]] : []), ["ok", "Close", true]]);
    if (answer === "tag") this.#edit("tag");
  }
  // -- end of accessibility --

  /** Print-production CMYK preflight & Output Preview: inspect DPI, TAC ink limits,
   *  overprint/knockout, PDF/X-1a & PDF/X-4 compliance, C/M/Y/K plate separations,
   *  TAC heatmap, click-to-jump, and one-click press fixes. */
  async checkPreflight() {
    const tab = this.activeTab;
    if (!tab || !this.doc) return;

    const useFastId = Boolean(tab.fileId && !tab.dirty && !(this.doc?.annotationStorage?.size > 0));
    let cachedBytes = null;
    const ensureBytes = async () => {
      if (!cachedBytes) cachedBytes = await this._currentBytes();
      return cachedBytes;
    };

    const appendDoc = async (form) => {
      if (useFastId) {
        form.append("id", tab.fileId);
      } else {
        const bytes = await ensureBytes();
        form.append("file", new Blob([bytes], { type: "application/pdf" }), tab.name);
      }
      if (this.password) form.append("password", this.password);
    };

    let curProfile = "standard";
    let curMaxTac = "300";
    let curPlate = "all";
    let curPage = Math.max(1, Math.min(this.doc.numPages || 1, this.pdfViewer?.currentPageNumber || 1));
    let blobUrls = [];

    const runCheck = async () => {
      const form = new FormData();
      await appendDoc(form);
      form.append("profile", curProfile);
      form.append("max_tac", curMaxTac);
      const res = await fetch("/api/preflight/check", {
        method: "POST", headers: { "X-Pdfm-Token": this.token }, body: form,
      });
      const json = await res.json();
      if (!res.ok) throw new Error(json.error || res.statusText);
      return json;
    };

    this._busy(true);
    let data;
    try {
      data = await runCheck();
    } catch (e) {
      this._busy(false);
      this.toast("Print preflight failed: " + (e?.message || e));
      return;
    }
    this._busy(false);

    const profileSel = el("select", { class: "rd-zoom", "aria-label": "Compliance profile" },
      el("option", { value: "standard" }, "Standard Press"),
      el("option", { value: "pdfx-1a" }, "PDF/X-1a (CMYK, flat)"),
      el("option", { value: "pdfx-4" }, "PDF/X-4 (CMYK + alpha)"));
    const tacSel = el("select", { class: "rd-zoom", "aria-label": "Maximum ink limit (TAC)" },
      el("option", { value: "300" }, "300% (Coated)"),
      el("option", { value: "280" }, "280% (Uncoated)"),
      el("option", { value: "320" }, "320% (Sheetfed)"));
    tacSel.value = curMaxTac;

    const controlsBar = el("div", { class: "rd-pf-controls" },
      el("label", {}, "Profile ", profileSel),
      el("label", {}, "Max ink (TAC) ", tacSel));

    const head = el("p", { class: "rd-preflight-summary" });
    const list = el("ul", { class: "rd-preflight-list" });

    // Output Preview: C / M / Y / K plate separation & TAC heatmap
    const plateDefs = [
      ["all", "Composite"],
      ["c", "Cyan"],
      ["m", "Magenta"],
      ["y", "Yellow"],
      ["k", "Black (K)"],
      ["tac", "TAC Heatmap"],
    ];
    const plateBtns = new Map();
    const plateBar = el("div", { class: "rd-pf-plates", role: "group", "aria-label": "CMYK plates and TAC preview" });
    for (const [pid, plabel] of plateDefs) {
      const b = el("button", {
        type: "button",
        class: `rd-btn rd-pf-plate rd-pf-plate-${pid}${pid === curPlate ? " on" : ""}`,
      }, plabel);
      b.addEventListener("click", () => {
        curPlate = pid;
        for (const [id, btn] of plateBtns) btn.classList.toggle("on", id === curPlate);
        refreshPreview();
      });
      plateBtns.set(pid, b);
      plateBar.append(b);
    }

    const prevPageBtn = el("button", { type: "button", class: "rd-btn" }, "‹");
    const pageLabel = el("span", { class: "rd-pf-page-status" }, `${curPage} / ${this.doc.numPages}`);
    const nextPageBtn = el("button", { type: "button", class: "rd-btn" }, "›");
    const previewMeta = el("span", { class: "rd-pf-preview-meta" });
    const previewNav = el("div", { class: "rd-pf-preview-nav" },
      prevPageBtn, pageLabel, nextPageBtn, el("span", { style: "flex:1" }), previewMeta);

    const previewImg = el("img", { class: "rd-pf-preview-img", alt: "CMYK plate & TAC output preview" });
    const inkReadout = el("div", { class: "rd-pf-ink-readout" }, "Hover preview for live C / M / Y / K & TAC%");
    const previewBox = el("div", { class: "rd-pf-preview-box" }, plateBar, previewNav, previewImg, inkReadout);

    const inkCache = new Map();
    const ensureInkGrid = async (pageNum) => {
      if (inkCache.has(pageNum)) return inkCache.get(pageNum);
      try {
        const form = new FormData();
        await appendDoc(form);
        form.append("page", String(pageNum));
        form.append("dpi", "72");
        const res = await fetch("/api/preflight/inkmap", {
          method: "POST", headers: { "X-Pdfm-Token": this.token }, body: form,
        });
        if (!res.ok) return null;
        const g = await res.json();
        const bin = atob(g.cmyk_b64 || "");
        const bytes = new Uint8Array(bin.length);
        for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
        const entry = { width: g.width, height: g.height, data: bytes };
        inkCache.set(pageNum, entry);
        return entry;
      } catch {
        return null;
      }
    };

    previewImg.addEventListener("mousemove", (ev) => {
      const grid = inkCache.get(curPage);
      if (!grid || !previewImg.naturalWidth || !previewImg.naturalHeight) return;
      const rect = previewImg.getBoundingClientRect();
      if (rect.width <= 0 || rect.height <= 0) return;
      // Account for object-fit: contain letterboxing inside previewImg
      const imgRatio = previewImg.naturalWidth / previewImg.naturalHeight;
      const boxRatio = rect.width / rect.height;
      let drawW = rect.width, drawH = rect.height, offX = 0, offY = 0;
      if (boxRatio > imgRatio) {
        drawW = rect.height * imgRatio;
        offX = (rect.width - drawW) / 2;
      } else {
        drawH = rect.width / imgRatio;
        offY = (rect.height - drawH) / 2;
      }
      const nx = (ev.clientX - rect.left - offX) / drawW;
      const ny = (ev.clientY - rect.top - offY) / drawH;
      if (nx < 0 || nx > 1 || ny < 0 || ny > 1) return;
      const gx = Math.min(grid.width - 1, Math.max(0, Math.floor(nx * grid.width)));
      const gy = Math.min(grid.height - 1, Math.max(0, Math.floor(ny * grid.height)));
      const idx = (gy * grid.width + gx) * 4;
      const c = grid.data[idx], m = grid.data[idx + 1], y = grid.data[idx + 2], k = grid.data[idx + 3];
      const tac = c + m + y + k;
      const over = tac > Number(curMaxTac);
      inkReadout.textContent = `C ${c}% · M ${m}% · Y ${y}% · K ${k}%  —  TAC ${tac}%${over ? " (OVER LIMIT)" : ""}`;
      inkReadout.classList.toggle("over", over);
    });
    previewImg.addEventListener("mouseleave", () => {
      inkReadout.textContent = "Hover preview for live C / M / Y / K & TAC%";
      inkReadout.classList.remove("over");
    });

    let previewSeq = 0;
    const refreshPreview = async () => {
      const seq = ++previewSeq;
      pageLabel.textContent = `Page ${curPage} / ${this.doc.numPages}`;
      previewMeta.textContent = "Rendering plate…";
      ensureInkGrid(curPage);
      try {
        const form = new FormData();
        await appendDoc(form);
        form.append("page", String(curPage));
        form.append("plate", curPlate);
        form.append("max_tac", curMaxTac);
        form.append("dpi", "96");
        const res = await fetch("/api/preflight/preview", {
          method: "POST", headers: { "X-Pdfm-Token": this.token }, body: form,
        });
        if (!res.ok || seq !== previewSeq) return;
        const maxTacHdr = res.headers.get("X-ZPDF-Max-TAC");
        const overPctHdr = res.headers.get("X-ZPDF-Over-TAC-Pct");
        const blob = await res.blob();
        if (seq !== previewSeq) return;
        const url = URL.createObjectURL(blob);
        blobUrls.push(url);
        previewImg.src = url;
        if (curPlate === "tac" && maxTacHdr) {
          previewMeta.textContent = `Peak TAC: ${maxTacHdr}% · Over ${curMaxTac}%: ${overPctHdr || "0"}%`;
        } else {
          previewMeta.textContent = curPlate === "all" ? "Composite preview" : `Plate: ${curPlate.toUpperCase()}`;
        }
      } catch {
        if (seq === previewSeq) previewMeta.textContent = "Preview unavailable";
      }
    };

    const renderReport = (rep) => {
      const s = rep.summary || { errors: 0, warnings: 0 };
      const tacStr = rep.max_tac_pct ? ` · Peak TAC: ${rep.max_tac_pct}%` : "";
      const spotStr = rep.spot_colors?.length ? ` · Spot inks: ${rep.spot_colors.join(", ")}` : "";
      head.textContent = (rep.passed ? "Ready to print" : `${s.errors} print error${s.errors === 1 ? "" : "s"} found`)
        + (s.warnings ? ` · ${s.warnings} warning${s.warnings === 1 ? "" : "s"}` : "")
        + tacStr + spotStr
        + (rep.color_spaces?.length ? ` · Colour spaces: ${rep.color_spaces.join(", ")}` : "");
      list.replaceChildren();
      if (!rep.issues?.length) {
        list.append(el("li", {}, "No print issues detected."));
      } else {
        for (const issue of rep.issues) {
          const badge = el("span", { class: `rd-pf-sev rd-pf-${issue.severity}` }, issue.severity.toUpperCase());
          const li = el("li", {}, badge, " ", issue.message);
          if (issue.page) {
            li.classList.add("rd-pf-jump");
            li.title = "Jump to page " + issue.page;
            li.addEventListener("click", () => {
              curPage = issue.page;
              this.goToPage(issue.page);
              refreshPreview();
            });
          }
          list.append(li);
        }
      }
    };

    prevPageBtn.addEventListener("click", () => {
      if (curPage > 1) { curPage--; refreshPreview(); }
    });
    nextPageBtn.addEventListener("click", () => {
      if (curPage < this.doc.numPages) { curPage++; refreshPreview(); }
    });
    const onControlChange = async () => {
      curProfile = profileSel.value;
      curMaxTac = tacSel.value;
      try {
        data = await runCheck();
        renderReport(data);
        if (curPlate === "tac") refreshPreview();
      } catch (e) {
        this.toast("Preflight update failed: " + (e?.message || e));
      }
    };
    profileSel.addEventListener("change", onControlChange);
    tacSel.addEventListener("change", onControlChange);

    renderReport(data);
    refreshPreview();

    const buttons = [["ok", "Close", true]];
    const codes = new Set((data.issues || []).map((i) => i.code));
    const hasBleed = codes.has("missing_bleed") || !data.page_sizes?.every((p) => p.has_bleed);
    if (codes.has("mixed_page_size")) buttons.unshift(["normalize", "Normalize page sizes", false]);
    if (hasBleed) buttons.unshift(["bleed", "Add 3 mm bleed + crop marks", false]);
    if (codes.has("white_overprint")) buttons.unshift(["white_op", "Fix white overprint", false]);
    const hasAutoFixable = ["white_overprint", "rich_black_text", "hairline_stroke", "high_tac", "rgb_color", "missing_output_intent", "missing_trapped_key"]
      .some((c) => codes.has(c));
    if (hasAutoFixable) buttons.unshift(["fix_all", "Fix all CMYK press issues", false]);

    const layoutWrap = el("div", { class: "rd-pf-grid" },
      el("div", { class: "rd-pf-col-issues" }, controlsBar, head, list),
      previewBox);

    const choice = await this._dialog("Print preflight & CMYK Output Preview", [layoutWrap], buttons);
    for (const u of blobUrls) URL.revokeObjectURL(u);

    if (choice === "normalize" || choice === "bleed" || choice === "white_op" || choice === "fix_all") {
      const fixAll = choice === "fix_all";
      await this._changeDocument(
        (bytes, t) => this._post("/api/preflight/fix", bytes, t, {
          normalize_size: choice === "normalize" ? "true" : "false",
          add_bleed_mm: choice === "bleed" ? "3" : "0",
          add_crop_marks: choice === "bleed" ? "true" : "false",
          fix_white_overprint: (choice === "white_op" || fixAll) ? "true" : "false",
          fix_rich_black_text: fixAll ? "true" : "false",
          clamp_tac: fixAll ? curMaxTac : "0",
          fix_hairlines: fixAll ? "0.25" : "0",
          convert_rgb_to_cmyk: fixAll ? "true" : "false",
          add_pdfx_intent: fixAll ? "true" : "false",
        }),
        { failure: "Could not apply print fix: " },
      );
      const msgMap = {
        normalize: "Normalized page sizes.",
        bleed: "Added 3 mm bleed and crop marks.",
        white_op: "Cleared overprint on white objects and text.",
        fix_all: `Applied CMYK press fixes (RGB→CMYK, TAC ≤ ${curMaxTac}%, hairlines, rich-black text, overprint, PDF/X OutputIntent).`,
      };
      this.toast(msgMap[choice]);
    }
  }

  /** Translate the currently visible page offline without modifying the PDF. */
  async _translateLanguages() {
    if (this._cachedTranslateLangs) return this._cachedTranslateLangs;
    const fallback = [
      ["en", "English"], ["es", "Español (Spanish)"], ["fr", "Français (French)"],
      ["de", "Deutsch (German)"], ["zh-Hans", "简体中文 (Chinese Simplified)"],
      ["zh-Hant", "繁體中文 (Chinese Traditional)"], ["ja", "日本語 (Japanese)"],
      ["ko", "한국어 (Korean)"], ["ar", "العربية (Arabic)"], ["pt", "Português (Portuguese)"],
      ["ru", "Русский (Russian)"], ["it", "Italiano (Italian)"],
    ];
    try {
      const res = await fetch("/api/ops", {
        headers: this.token ? { "X-Pdfm-Token": this.token } : {},
      });
      if (res.ok) {
        const ops = await res.json();
        const tr = Array.isArray(ops) ? ops.find((o) => o.id === "translate") : null;
        const field = tr?.fields?.find((f) => f.name === "target_lang");
        if (Array.isArray(field?.choices) && field.choices.length) {
          this._cachedTranslateLangs = field.choices;
          return this._cachedTranslateLangs;
        }
      }
    } catch (_) {}
    return fallback;
  }

  async translateCurrentPage(opts = null) {
    const tab = this.activeTab;
    if (!tab?.doc) return;
    let fromCode = opts?.from ?? store.get("pdfm-tr-from", "auto");
    let toCode = opts?.to ?? store.get("pdfm-tr-to", "es");
    if (!opts?.skipPicker) {
      const langs = await this._translateLanguages();
      const mkSel = (list, def) => {
        const sel = el("select");
        for (const [code, label] of list) {
          const opt = el("option", { value: code }, label);
          if (code === def) opt.selected = true;
          sel.append(opt);
        }
        return sel;
      };
      const fromSel = mkSel([["auto", "Auto-detect"], ...langs], fromCode);
      const toSel = mkSel(langs, toCode);
      const swapBtn = el("button", {
        type: "button",
        class: "btn sm",
        title: "Swap source and target languages",
        style: "align-self:flex-end;margin-bottom:2px;",
      }, "⇄ Swap");
      const syncSwap = () => { swapBtn.disabled = fromSel.value === "auto"; };
      syncSwap();
      fromSel.addEventListener("change", syncSwap);
      swapBtn.addEventListener("click", () => {
        if (fromSel.value === "auto") return;
        const tmp = fromSel.value;
        fromSel.value = toSel.value;
        toSel.value = tmp;
        syncSwap();
      });
      const form = el("div", { class: "rd-translate-pick" },
        el("label", { class: "dlg-field" }, el("span", {}, "From language"), fromSel),
        el("label", { class: "dlg-field" }, el("span", {}, "To language"), toSel),
        swapBtn,
      );
      const go = await this._dialog(`Translate page ${this.currentPage}`, [form], [
        ["cancel", "Cancel", false],
        ["go", "Translate", true],
      ]);
      if (go !== "go") return;
      fromCode = fromSel.value;
      toCode = toSel.value;
      store.set("pdfm-tr-from", fromCode);
      store.set("pdfm-tr-to", toCode);
    }

    const pageNum = this.currentPage;
    const runTranslate = async () => {
      const fd = new FormData();
      // Fast path: reference the already-open file by ID when unmodified instead of re-uploading the whole PDF.
      if (tab.fileId && this.isPristine()) {
        fd.append("id", tab.fileId);
      } else {
        const bytes = await this._currentBytes();
        fd.append("file", new Blob([bytes], { type: "application/pdf" }), tab.name || "document.pdf");
      }
      fd.append("page", String(pageNum));
      fd.append("from", fromCode);
      fd.append("to", toCode);
      if (tab.password) fd.append("password", tab.password);
      const res = await fetch("/api/translate/page", {
        method: "POST",
        headers: this.token ? { "X-Pdfm-Token": this.token } : {},
        body: fd,
      });
      const payload = await res.json();
      if (!res.ok) throw new Error(payload.error || res.statusText);
      return payload;
    };

    this.toast(`Translating page ${pageNum}…`);
    let data;
    try {
      data = await runTranslate();
    } catch (e) {
      const msg = e?.message || String(e);
      if (/language pack|madlad|pack not installed/i.test(msg)) {
        const smallPacks = new Set([
          "ar", "az", "bg", "bn", "ca", "cs", "da", "de", "el", "eo", "es", "et", "eu", "fa", "fi",
          "fr", "ga", "gl", "he", "hi", "hu", "id", "it", "ja", "ko", "lt", "lv", "ms", "nb", "nl",
          "no", "pl", "pt", "ro", "ru", "sk", "sl", "sq", "sr", "sv", "th", "tl", "tr", "uk", "ur",
          "vi", "zh", "zh-Hans", "zh-Hant",
        ]);
        const explicit = [...new Set([fromCode, toCode].filter((c) => c && c !== "en" && c !== "auto"))];
        const useMadlad = !explicit.length || explicit.some((c) => !smallPacks.has(c));
        const needed = useMadlad ? ["madlad-400-3b"] : explicit;
        const packLabel = useMadlad ? "MADLAD-400 (400+ languages)" : `pack (${needed.join(", ")})`;
        const dlChoice = await this._dialog("Offline translation", [el("p", {}, msg)], [
          ["cancel", "Close", false],
          ["download", `Download ${packLabel}`, true],
        ]);
        if (dlChoice !== "download") return;
        this.toast(`Downloading ${packLabel}…`);
        try {
          for (const code of needed) {
            const dlRes = await fetch("/api/i18n/pack/download", {
              method: "POST",
              headers: {
                "Content-Type": "application/json",
                ...(this.token ? { "X-Pdfm-Token": this.token } : {}),
              },
              body: JSON.stringify({ code }),
            });
            const dlData = await dlRes.json();
            if (!dlRes.ok) throw new Error(dlData.error || dlRes.statusText);
          }
          this.toast(`Translating page ${pageNum}…`);
          data = await runTranslate();
        } catch (dlErr) {
          await this._dialog("Translation", [el("p", {}, dlErr?.message || String(dlErr))], [["ok", "Close", true]]);
          return;
        }
      } else {
        await this._dialog("Translation", [el("p", {}, msg)], [["ok", "Close", true]]);
        return;
      }
    }

    const translatedText = data.translated || "(No extractable text on this page — run OCR first.)";
    const originalText = data.original || "(No extractable text on this page.)";
    let showingOriginal = false;
    const out = el("textarea", {
      readonly: "readonly",
      dir: "auto",
      rows: "14",
      style: "width:100%;font:inherit;line-height:1.55;padding:10px;box-sizing:border-box;",
    });
    out.value = translatedText;

    const btnTrans = el("button", { type: "button", class: "btn sm seal" }, "Translated");
    const btnOrig = el("button", { type: "button", class: "btn sm" }, "Original");
    const setView = (orig) => {
      showingOriginal = orig;
      out.value = orig ? originalText : translatedText;
      btnTrans.classList.toggle("seal", !orig);
      btnOrig.classList.toggle("seal", orig);
    };
    btnTrans.addEventListener("click", () => setView(false));
    btnOrig.addEventListener("click", () => setView(true));
    const bar = el("div", { style: "display:flex;gap:6px;margin-bottom:8px;align-items:center;" }, btnTrans, btnOrig);

    const hasNext = pageNum < (tab.doc.numPages || 1);
    const buttons = [
      ["copy", "Copy text", false],
      ...(hasNext ? [["next", "Next page →", false]] : []),
      ["ok", "Close", true],
    ];
    const choice = await this._dialog(
      `Page ${data.page} (${data.src_lang} → ${data.dst_lang})`,
      [bar, out],
      buttons,
    );
    if (choice === "copy" && navigator.clipboard?.writeText) {
      try { await navigator.clipboard.writeText(out.value); this.toast(showingOriginal ? "Copied original text." : "Copied translation."); } catch (_) {}
    } else if (choice === "next" && hasNext) {
      this.goToPage(pageNum + 1);
      await this.translateCurrentPage({ from: fromCode, to: toCode, skipPicker: true });
    }
  }

  async openMangaLens(opts = {}) {
    if (!this.doc) return;
    if (!this.mangaLens) this.mangaLens = new MangaLens(this);
    if (this.mangaLens.active && opts.toggle) {
      this.mangaLens.close();
      return;
    }
    await this.mangaLens.open(opts);
  }

  /** A toast that explains the tool just chosen: it goes when that tool or
   *  its bar does, or when another bar opens (see #endHint). */
  /** Open a link that leaves the document, in the browser: only web, mail
   *  and phone addresses, and (unless turned off in the More menu) after
   *  showing where it really goes, which the words on the page may not say. */
  async _openLink(url) {
    let address = null;
    try { address = new URL(url, location.href); } catch (e) { address = null; }
    if (!address || !["http:", "https:", "mailto:", "tel:"].includes(address.protocol)) {
      this.toast("That link isn't a web address, so ZPDF won't open it.", 6000);
      return;
    }
    if (store.get("pdfm-link-ask", true)) {
      const web = address.protocol.startsWith("http");
      const rest = web ? address.href.slice((address.protocol + "//" + address.host).length) : address.href.slice(address.protocol.length);
      const shown = el("p", { class: "rd-linkaddr" }, web ? address.protocol + "//" : address.protocol,
        el("strong", {}, web ? address.host : ""), rest.length > 300 ? rest.slice(0, 300) + "…" : rest);
      const again = el("input", { type: "checkbox" });
      const notes = [];
      if (address.protocol === "http:") notes.push("This address isn't protected (http): what you type there can be read on the way.");
      if (web && (/^xn--|\.xn--/.test(address.hostname) || /^[\d.]+$|^\[/.test(address.hostname))) notes.push("The site's name is unusual (numbers, or letters made to look like others). Check it carefully.");
      const answer = await this._dialog("Open this link?", [
        el("p", {}, web ? "It goes to this address, whatever the words on the page say:" : address.protocol === "tel:" ? "It will start a call to:" : "It will start a message to:"), shown,
        ...notes.map((n) => el("p", { class: "rd-linknote" }, n)),
        el("label", { class: "rd-check" }, again, "Don't ask again (More menu → Ask before opening web links)"),
      ], [["cancel", "Cancel"], ["ok", web ? "Open in browser" : address.protocol === "tel:" ? "Start the call" : "Start the message", true]]);
      if (answer !== "ok") return;
      if (again.checked) store.set("pdfm-link-ask", false);
    }
    window.open(address.href, "_blank", "noopener,noreferrer");
  }

  _hint(message, ms) {
    this.toast(message, ms);
    this.hintEl = this.toastEl;
  }
  _endHint() {
    if (this.hintEl && this.hintEl === this.toastEl) { clearTimeout(this.toastTimer); this.toastEl.remove(); this.toastEl = null; }
    this.hintEl = null;
  }

  toast(message, ms = 4000, action = null) {
    if (!message) return;
    if (this.toastEl) this.toastEl.remove();
    const t = el("div", { class: "rd-toast", role: "status" }, el("span", {}, message));
    // ``action``: [label, run] for one button, or [[label, run, "seal"], [label, run]] for more.
    const actions = !action ? [] : Array.isArray(action[0]) ? action : [action];
    for (const [label, run, kind] of actions) t.append(el("button", { type: "button", class: kind || null, onclick: run }, label));
    if (this.scalebar && !this.scalebar.hidden) {  // over the drawing's top, not its title block
      t.classList.add("top");
      t.style.top = `${this.body.offsetTop + 12}px`;
    }
    this.root.append(t);
    this.toastEl = t;
    clearTimeout(this.toastTimer);
    this.toastHold?.();
    this.toastHold = null;
    // A toast with a button (Undo, Save…) stays 10 s at least, and waits while it's pointed at or focused.
    if (action) this.toastHold = heldTimer(t, Math.max(ms, 10000), () => fadeOut(t));
    else this.toastTimer = setTimeout(() => fadeOut(t), ms);
  }


  // ---- for the command palette and the page menu ----------------------------------------
  /** Switch on an annotation tool or Edit text (EDITTEXT); NONE goes back to reading. */
  useTool(name) {
    if (!this.doc) return;
    if (name === "NONE") { this.closeAnnotate(); this.closeCheckOcr(); return; }
    if (name === "CHECKOCR") { this.openCheckOcr(); return; }
    this.openAnnotate();
    this._setEditMode(name, name === "STAMP");
  }
  editWith(toolId) { return this.#edit(toolId); }


  /** What there is to do with the open document, for the command palette:
   *  [{label, keys?, words?, run}]. */
  commands() {
    const open = { icon: "open", label: "Open a PDF", keys: "Ctrl+O", words: "file browse", run: () => this.chooseFile() };
    if (!this.doc) return [open];
    const v = this.pdfViewer;
    // (Their single keys work while the annotate bar is open.)
    const tool = (label, mode, words = "", icon = "pen", keys = "") => ({ icon, label, words: "annotate " + words, run: () => this.useTool(mode), ...(keys ? { keys } : {}) });
    const list = [
      { icon: "edittext", label: "Edit text", keys: "E", words: "change words typo rewrite paragraph add text", run: () => this.useTool("EDITTEXT") },
      { icon: "edittext", label: "Check OCR", words: "ocr unsure words fix recognised text scan mistakes", run: () => this.openCheckOcr() },
      { icon: "search", label: "Find in document", keys: "Ctrl+F", words: "search look for", run: () => this.openFind() },
      tool("Highlight text", "HIGHLIGHT", "mark marker", "highlight", "H"), tool("Add text", "FREETEXT", "text box note comment type", "text", "A"),
      tool("Draw", "INK", "pen ink freehand", "draw", "D"), tool("Add a picture", "STAMP", "image photo stamp", "image"),
      tool("Add a note", "NOTE", "comment sticky review", "note", "C"), tool("Underline text", "UNDERLINE", "markup", "underline"),
      tool("Strike out text", "STRIKEOUT", "strikethrough delete markup", "strike"),
      tool("Draw a shape", "SHAPE", "rectangle ellipse circle cloud arrow line markup revision", "shapes"),
      tool("Stamp the page", "STAMPS", "approved draft confidential final received reviewed rubber stamp", "stamp"),
      tool("Edit pictures", "IMAGES", "image photo move resize replace delete logo", "imgedit"),
      tool("Edit links", "LINKS", "hyperlink url web address page add change remove", "link"),
      { icon: "fillable", label: "Prepare form", words: "form fields fillable design edit add text checkbox radio dropdown signature button", run: () => this.openFormMode() },
      tool("Digital ID", "CERTSIGN", "sign with a digital id certificate pfx p12 digital signature sign", "badge"),
      tool("Redact this document", "REDACT", "redaction black out for good remove hide censor private confidential", "redact"),
      { icon: "badge", label: "Check the signatures", words: "verify valid certificate signed", run: () => this._signatureDetails() },
      { icon: "comments", label: "Comments", words: "notes replies review list markup", run: () => { this._setSidebar(true); this._showTab("comments"); } },
      { icon: "sign", label: "Sign", sub: "draw or type your signature", words: "signature autograph pen draw type initials", run: () => this.sign() },
      { icon: "cw", label: "Rotate the view clockwise", keys: "R", words: "turn right", run: () => this.rotate(90) },
      { icon: "ccw", label: "Rotate the view counter-clockwise", keys: "Shift+R", words: "turn left anticlockwise", run: () => this.rotate(-90) },
      { icon: "cw", label: "Turn this page clockwise (in the file)", words: "rotate page save", run: () => this.rotatePages([v.currentPageNumber], 90) },
      { icon: "ccw", label: "Turn this page counter-clockwise (in the file)", words: "rotate page save", run: () => this.rotatePages([v.currentPageNumber], -90) },
      { icon: "trash", label: "Delete this page", words: "remove page", run: () => this.deletePages([v.currentPageNumber]) },
      { icon: "copypage", label: "Duplicate this page", words: "copy page", run: () => this.duplicatePages([v.currentPageNumber]) },
      { icon: "save", label: "Extract this page as a new PDF", words: "split save page", run: () => this.extractPages([v.currentPageNumber]) },
      { icon: "open", label: "Insert a PDF after this page", words: "add merge combine pages", run: () => this.insertPdf(v.currentPageNumber) },
      { icon: "sidebar", label: this.sidebar.hidden ? "Show thumbnails" : "Hide thumbnails", words: "sidebar pages", run: () => this.toggleSidebar() },
      { icon: "book", label: "Show bookmarks", words: "outline contents chapters sidebar", run: () => this.showBookmarks() },
      ...(this.layers ? [{ icon: "layers", label: "Show layers", words: "optional content cad drawing hide sidebar",
        run: () => { this.layersWanted = true; this._setSidebar(true); this._showTab("layers"); } }] : []),
      { icon: "full", label: "Fit page", words: "zoom whole", run: () => { v.currentScaleValue = "page-fit"; } },
      { icon: "spread", label: "Fit width", words: "zoom", run: () => { v.currentScaleValue = "page-width"; } },
      { icon: "plus", label: "Actual size", words: "zoom 100%", run: () => { v.currentScaleValue = "page-actual"; } },
      { icon: "ruler", label: "True paper size", words: "zoom real scale blueprint plan drawing", run: () => this._viewAt("true") },
      { icon: "ruler", label: "Measure a distance", keys: "M", words: "length dimension blueprint plan scale ruler snap", run: () => this.measureTool("distance") },
      { icon: "area", label: "Measure an area", words: "room square feet metres blueprint plan scale", run: () => this.measureTool("area") },
      { icon: "rect", label: "Measure a rectangle", words: "room area two corners blueprint takeoff", run: () => this.measureTool("rect") },
      { icon: "cutout", label: "Cut an opening out of an area", words: "subtract hole stair shaft column deduct takeoff", run: () => this.measureTool("cutout") },
      { icon: "count", label: "Count things", words: "outlets doors fixtures windows tally takeoff", run: () => this.measureTool("count") },
      { icon: "angle", label: "Measure an angle", words: "degrees corner slope", run: () => this.measureTool("angle") },
      { icon: "layers", label: this.overlay ? "Stop comparing revisions" : "Compare with another revision", words: "overlay difference drawing changes old new version",
        run: () => (this.overlay ? this.closeCompare() : this.compareRevision()) },
      { icon: "csv", label: "Save a quantity schedule (Excel)", words: "xlsx spreadsheet export takeoff quantities totals measurements dimensions", run: () => this._exportQuantities("xlsx") },
      { icon: "csv", label: "Save a quantity schedule (CSV)", words: "spreadsheet export takeoff quantities measurements", run: () => this._exportQuantities("csv") },
      { icon: "pdfin", label: "Put the measurements in the PDF", words: "annotations save keep markup", run: () => this._measuresIntoPdf() },
      { icon: "path", label: "Measure a run of lines (path)", words: "polyline walls total length baseboard blueprint", run: () => this.measureTool("path") },
      { icon: "list", label: "List of measurements", words: "totals copy units blueprint takeoff", run: () => { this.toggleScaleBar(true); this._toggleMeasPanel(true); } },
      { icon: "straight", label: this.straightOnly ? "Measure at any angle" : "Measure straight lines only", words: "horizontal vertical orthogonal blueprint", run: () => this.straightBtn.click() },
      { icon: "ruler", label: "Set the drawing scale from a dimension", words: "blueprint plan calibrate known length", run: () => this.measureTool("calibrate") },
      { icon: "ruler", label: "Drawing scale", words: "blueprint plan sheet show at measure", run: () => this.toggleScaleBar(true) },
      { icon: "screen", label: "Calibrate the screen for true size", words: "monitor dpi ppi real size card", run: () => this.calibrateScreen() },
      { icon: "plus", label: "Zoom to a percentage…", words: "custom zoom type number", run: () => { this.zoomInput.focus(); } },
      { icon: "plus", label: "Zoom in", keys: "Ctrl +", run: () => v.increaseScale() },
      { icon: "minus", label: "Zoom out", keys: "Ctrl −", run: () => v.decreaseScale() },
      { icon: "up", label: "First page", keys: "Home", words: "start beginning", run: () => this.goToPage(1) },
      { icon: "down", label: "Last page", keys: "End", words: "end", run: () => this.goToPage(v.pagesCount) },
      { icon: "spread", label: "Two-page view", words: "spread book", run: () => this.toggleSpread() },
      { icon: "spread", label: "Cover page alone (align two-page spreads)", words: "spread cover offset even odd manga book", run: () => this.toggleCoverOffset() },
      { icon: "book", label: "Right-to-left manga reading", words: "rtl manga comic japanese direction spread", run: () => this.toggleRtlReading() },
      { icon: "spread", label: this.splitOpen ? "Close split view" : "Split view (side by side)", keys: "Ctrl+\\", words: "compare side by side two panes dual view", run: () => this.toggleSplitView() },
      ...(this.splitOpen ? [
        { icon: "spread", label: "Switch split pane", keys: "F6", words: "focus left right pane", run: () => this.switchSplitPane() },
        { icon: "spread", label: this.splitSync.checked ? "Turn off sync scroll in split view" : "Turn on sync scroll in split view", words: "lock scroll compare", run: () => { this.splitSync.checked = !this.splitSync.checked; } },
      ] : []),
      { icon: "moon", label: "Page theme: Smart Night", words: "dark pages night invert reading", run: () => this.setPageTheme(this.pageTheme() === "night" ? "light" : "night") },
      { icon: "book", label: "Page theme: Warm Sepia", words: "sepia warm paper reading comfort", run: () => this.setPageTheme(this.pageTheme() === "sepia" ? "light" : "sepia") },
      { icon: "screen", label: "Page theme: E-Ink High Contrast", words: "eink grayscale monochrome contrast paper", run: () => this.setPageTheme(this.pageTheme() === "eink" ? "light" : "eink") },
      { icon: "layers", label: "Workspace sessions (save / restore tabs)", words: "workspace session tabs split view restore save", run: () => this.openWorkspaceSessions() },
      { icon: "edittext", label: "Edit all OCR text-layer words", words: "ocr typo fix invisible text layer replace", run: () => this.openCheckOcr(null, { allWords: true }) },
      { icon: "hand", label: "Hand tool", words: "drag scroll pan", run: () => this.toggleHand() },
      { icon: "full", label: "Full screen", words: "presentation", run: () => this.toggleFullscreen() },
      { icon: "print", label: "Print", keys: "Ctrl+P", run: () => this.print() },
      { icon: "rect", label: "Crop pages visually", words: "trim margins box cut", run: () => this.openCropMode() },
      { icon: "print", label: "Print preflight check", words: "dpi cmyk rgb bleed fonts hairline press", run: () => this.checkPreflight() },
      { icon: "edittext", label: "Translate this page", words: "translate language offline page spanish french german chinese", run: () => this.translateCurrentPage() },
      { icon: "edittext", label: "Manga Lens (translate page overlay & bubble editor)", keys: "Shift+T", words: "manga lens comic bubble translate overlay edit scanlation", run: () => this.openMangaLens() },
      { icon: "split", label: "Live bilingual reading", words: "translate bilingual book split view side by side language", run: () => this.openSplitView("__translate__") },
      { icon: "command", label: "Ask this PDF (Local AI)", words: "ai assistant ask question ollama lm studio local llm chat citations", run: () => openAiAssistant(this, "ask") },
      { icon: "command", label: "Summarize PDF (Local AI)", words: "ai summary chapter bullets study notes ollama local llm", run: () => openAiAssistant(this, "summarize") },
      { icon: "command", label: "Extract table / key-values to CSV or JSON (Local AI)", words: "ai extract invoice table csv json structured ollama local llm", run: () => openAiAssistant(this, "extract") },
      ...(this.activeTab?.fileId ? [{ icon: "save", label: "Save", keys: "Ctrl+S", words: "write file", run: () => this.save() }] : []),
      { icon: "save", label: this.activeTab?.fileId ? "Save as" : this._edited() ? "Save a copy with your changes" : "Save a copy",
        keys: this.activeTab?.fileId ? "Ctrl+Shift+S" : "Ctrl+S", words: "download export copy", run: () => this.saveCopy() },
      { icon: "info", label: "Document properties", words: "info metadata author title", run: () => this.showProperties() },
      { icon: "x", label: "Close this document", words: "tab", run: () => this.closeTab() },
      open,
    ];
    return list.map((c) => ({ ...c, svg: svg(c.icon || "book") }));
  }

  async #edit(toolId) {
    this._markFlush();   // (a markup's arrow presses not written yet go in first)
    await this.chain;
    if (!this.doc || !this.onEdit) return;
    const bytes = await this._currentBytes();
    const file = new File([bytes], this._copyName(), { type: "application/pdf" });
    if (this.isPristine()) file.openedId = this.activeTab.fileId;  // the same file handed over twice is one file
    this.onEdit(toolId, file, this.password);
  }
}
