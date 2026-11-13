"""What a password allows a protected PDF's reader to do, in plain words
and as pypdf's flags. Used by core.encrypt and by filetools (which makes
the permissions and re-exports these); it imports neither, so the two
don't have to import each other."""

CHANGES_TEXT = {
    "any": "any changes except extracting pages",
    "comments": "commenting, filling in forms and signing",
    "forms": "filling in forms and signing",
    "pages": "inserting, deleting and turning pages",
    "none": "no changes",
}


def restricts(perms):
    """Whether any of ``perms`` is taken away (accessibility aside)."""
    return perms is not None and not all(
        getattr(perms, name) for name in ("extract", "print_highres", "print_lowres",
                                          "modify_other", "modify_annotation", "modify_form",
                                          "modify_assembly"))


def describe(perms):
    """Plain words for what a protected PDF allows."""
    if perms is None or not restricts(perms):
        return "everything is allowed"
    printing = ("printing" if perms.print_highres else
                "printing in low quality" if perms.print_lowres else "no printing")
    if perms.modify_other:
        changes = "any changes"
    elif perms.modify_annotation:
        changes = CHANGES_TEXT["comments"]
    elif perms.modify_form:
        changes = CHANGES_TEXT["forms"]
    elif perms.modify_assembly:
        changes = CHANGES_TEXT["pages"]
    else:
        changes = "no changes"
    return f"{printing}, {changes}, " + ("copying allowed" if perms.extract else "no copying")


def pypdf_flags(perms):
    """The same permissions for pypdf's encrypt (when qpdf can't read a file)."""
    from pypdf.constants import UserAccessPermissions as P

    flags = P.R1 | P.R2 | P.R7 | P.R8 | P.R13 | P.R14 | P.R15 | P.R16 | P.R17 | P.R18 | \
        P.R19 | P.R20 | P.R21 | P.R22 | P.R23 | P.R24 | P.R25 | P.R26 | P.R27 | P.R28 | \
        P.R29 | P.R30 | P.R31 | P.R32 | P.EXTRACT_TEXT_AND_GRAPHICS
    for on, flag in ((perms.print_lowres, P.PRINT),
                     (perms.print_highres, P.PRINT_TO_REPRESENTATION),
                     (perms.modify_other, P.MODIFY), (perms.extract, P.EXTRACT),
                     (perms.modify_annotation, P.ADD_OR_MODIFY), (perms.modify_form, P.FILL_FORM_FIELDS),
                     (perms.modify_assembly, P.ASSEMBLE_DOC)):
        if on:
            flags |= flag
    return flags
