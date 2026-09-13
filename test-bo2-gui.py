#!/usr/bin/env python3
"""
Regression tests for the header parsing in bo2-gui.py.

One failure class is the reason these exist. A field printed in a form the
parser does not recognise is dropped, the stock BLES01717 value is substituted
for it, and the file is re-signed carrying a value that was never in it. The
result looks correct afterwards: it boots on the console it was built on and
scetool -i reports the substituted value back quite happily, so there is
nothing to notice by inspection. The tests therefore assert what reaches
scetool's command line, not what the regex returns.

    python3 test-bo2-gui.py
"""

import importlib.util
import os
import struct
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location(
    "bo2_gui", os.path.join(HERE, "bo2-gui.py"))
gui = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gui)

EBOOT = gui.SLOTS[0]
STOCK_APP_VERSION = gui.FIELD_DEFAULTS["app_version"]

HEADER = """[*] SCE Header:
 Magic           0x53434500 [OK]
 Version         0x00000002
 Key Revision    {key_revision}
 Header Type     [SELF]
[*] Application Info:
 Auth-ID         0x1010000001000003 [Console]
 Vendor-ID       0x01000002
 SELF-Type       [NPDRM]
{version_line}
[*] NPDRM Info:
 Version         0x00000001
 DRM Type        [Free]
 App Type        [UEXEC]
 ContentID       UP0002-BLUS31011_00-CODBLOPS2PATCH09
 CID-FN Hash     A1B2C3D4E5F60718293A4B5C6D7E8F90
"""


def header(version_line=" Version         01.0000", key_revision="0x1C"):
    return HEADER.format(version_line=version_line, key_revision=key_revision)


class Recorder:
    """Stands in for App. _read_header and _sign only reach for these two."""

    def __init__(self, output=""):
        self.output = output
        self.args = []
        self.lines = []

    def _scetool(self, scetool, args, what):
        self.args.append(args)
        return self.output

    def log(self, text=""):
        self.lines.append(text)


def flag(args, name):
    """The value scetool was handed for a given switch."""
    return args[args.index(name) + 1]


def sign_args(info, slot=EBOOT):
    """Run the real signing path and hand back the command line it built."""
    recorder = Recorder()
    with tempfile.TemporaryDirectory() as workdir:
        elf = os.path.join(workdir, "in.elf")
        out = os.path.join(workdir, "out.self")
        for path in (elf, out):
            with open(path, "wb") as handle:
                handle.write(b"\x7fELF")
        gui.App._sign(recorder, "scetool", info, slot, elf, out)
    return recorder.args[0]


def field_line(lines, title):
    """The one log line for a field. The firmware version is absent from real
    scetool output, so a whole-log search for the fallback warning would match
    that instead of the field under test."""
    return next(line for line in lines if line.strip().startswith(title))


def read_header(text, slot=EBOOT):
    recorder = Recorder(text)
    info = gui.App._read_header(recorder, "scetool", "input.bin", slot)
    return info, recorder.lines


class VersionParsing(unittest.TestCase):
    """-A and -6 want 16 hex digits; builds print the value four ways."""

    def test_recognised_forms(self):
        cases = [
            ("01.0000", "0001000000000000"),
            ("04.0020", "0004002000000000"),
            ("02.0100", "0002010000000000"),
            ("01.00", "0001000000000000"),
            ("0001000000000000", "0001000000000000"),
            # scetool -r prints the raw field, and the 0x prefix used to defeat
            # the word boundary in the 16 digit branch.
            ("0x0002000000000000", "0002000000000000"),
            ("0x0004002000000000 [4.20]", "0004002000000000"),
            ("  0x0001000000000000  ", "0001000000000000"),
        ]
        for printed, expected in cases:
            with self.subTest(printed=printed):
                self.assertEqual(gui._version_value(printed), expected)

    def test_rejected_forms(self):
        for printed in ("", "not a version", "1.0.0", "[Console]"):
            with self.subTest(printed=printed):
                self.assertIsNone(gui._version_value(printed))


class VersionReachesScetool(unittest.TestCase):
    """The parser returning the right value is not enough on its own: the
    substitution happens in _read_header, after parsing."""

    def test_non_stock_version_is_not_replaced_by_the_stock_one(self):
        info, lines = read_header(
            header(version_line=" Version         0x0002000000000000"))
        self.assertEqual(info["app_version"], "0002000000000000")
        self.assertEqual(flag(sign_args(info), "-A"), "0002000000000000")
        self.assertNotEqual(flag(sign_args(info), "-A"), STOCK_APP_VERSION)
        self.assertNotIn("not in this header",
                         field_line(lines, "App version"))

    def test_a_version_equal_to_the_stock_one_is_still_read_not_defaulted(self):
        info, lines = read_header(header(version_line=" Version         01.0000"))
        self.assertEqual(flag(sign_args(info), "-A"), "0001000000000000")
        # Same value either way, so the log is the only thing that distinguishes
        # having read it from having given up and substituted it.
        self.assertNotIn("not in this header",
                         field_line(lines, "App version"))

    def test_a_missing_version_falls_back_loudly(self):
        info, lines = read_header(header(version_line=""))
        self.assertEqual(info["app_version"], STOCK_APP_VERSION)
        self.assertIn("not in this header", field_line(lines, "App version"))
        joined = "\n".join(lines)
        self.assertIn("WARNING", joined)
        self.assertIn("BLES01717", joined)

    def test_the_version_is_not_taken_from_another_section(self):
        # SCE Header and NPDRM Info both carry a Version line of their own.
        info, _ = read_header(header(version_line=""))
        self.assertEqual(info["app_version"], STOCK_APP_VERSION)


class FixedWidthFields(unittest.TestCase):
    """Over-printed hex used to be passed through at the width it was printed."""

    def test_key_revision_width(self):
        for printed in ("0x1C", "0x001C", "0x0000001C"):
            with self.subTest(printed=printed):
                info, _ = read_header(header(key_revision=printed))
                self.assertEqual(flag(sign_args(info), "-2"), "1C")

    def test_auth_id_is_the_hex_not_the_bracket(self):
        info, _ = read_header(header())
        self.assertEqual(flag(sign_args(info), "-3"), "1010000001000003")

    def test_a_value_too_wide_for_its_field_is_refused(self):
        self.assertIsNone(gui._hex_value("0x1C1C1C", 2))


class ContentId(unittest.TestCase):
    def test_accepted(self):
        self.assertEqual(gui._content_id(" UP0002-BLUS31011_00-CODBLOPS2PATCH09"),
                         "UP0002-BLUS31011_00-CODBLOPS2PATCH09")

    def test_padding_and_brackets_stripped(self):
        for printed in (" [UP0002-BLUS31011_00-CODBLOPS2PATCH09]",
                        " UP0002-BLUS31011_00-CODBLOPS2PATCH09\x00\x00"):
            with self.subTest(printed=printed):
                self.assertEqual(gui._content_id(printed),
                                 "UP0002-BLUS31011_00-CODBLOPS2PATCH09")

    def test_rubbish_refused_rather_than_passed_to_f(self):
        self.assertIsNone(gui._content_id(" not a content id!"))


class Klicensee(unittest.TestCase):
    def test_eboot_is_signed_without_one(self):
        info, _ = read_header(header())
        self.assertNotIn("-l", sign_args(info, gui.SLOTS[0]))

    def test_the_selfs_are_signed_with_one(self):
        info, _ = read_header(header())
        for slot in gui.SLOTS[1:]:
            with self.subTest(slot=slot.label):
                args = sign_args(info, slot)
                self.assertEqual(flag(args, "-l"), gui.KLICENSEE)
                # -g is the name on the console, which the CID_FN hash covers.
                self.assertEqual(flag(args, "-g"), slot.out_name)


def build_param_sfo(pairs):
    """A real PARAM.SFO: 0x14 header, 16 byte index entries, then a key table
    and a data table."""
    index = b""
    keys = b""
    values = b""
    for key, value in pairs:
        if isinstance(value, int):
            fmt, raw = 0x0404, struct.pack("<I", value)
        else:
            fmt, raw = 0x0204, value.encode("utf-8") + b"\x00"
        padded = raw + b"\x00" * (-len(raw) % 4)
        index += struct.pack("<HHIII", len(keys), fmt, len(raw), len(padded),
                             len(values))
        keys += key.encode("ascii") + b"\x00"
        values += padded
    keys += b"\x00" * (-len(keys) % 4)
    key_table = 0x14 + len(index)
    data_table = key_table + len(keys)
    header = struct.pack("<4sIIII", b"\x00PSF", 0x0101, key_table, data_table,
                         len(pairs))
    return header + index + keys + values


class ParamSfo(unittest.TestCase):
    """The title update number lives here and nowhere else. Every BO2 SELF
    reports app version 01.00 whatever update is installed, so deriving it
    from the header gives the same wrong answer on every machine."""

    def test_values_are_read_back(self):
        raw = build_param_sfo([("APP_VER", "01.19"),
                               ("ATTRIBUTE", 32),
                               ("TITLE", "Call of Duty: Black Ops II"),
                               ("TITLE_ID", "BLUS31011")])
        values = gui.parse_param_sfo(self._written(raw))
        self.assertEqual(values["APP_VER"], "01.19")
        self.assertEqual(values["TITLE_ID"], "BLUS31011")
        self.assertEqual(values["TITLE"], "Call of Duty: Black Ops II")
        self.assertEqual(values["ATTRIBUTE"], "32")

    def test_update_is_formatted_for_display(self):
        for stored, shown in [("01.19", "1.19"), ("01.00", "1.00"),
                              ("02.05", "2.05"), ("1.19", "1.19")]:
            with self.subTest(stored=stored):
                self.assertEqual(gui.update_words(stored), shown)

    def test_rubbish_is_dropped_rather_than_guessed(self):
        for stored in ("", "  ", "01", "0001000000000000", "unknown"):
            with self.subTest(stored=stored):
                self.assertEqual(gui.update_words(stored), "")

    def test_a_file_that_is_not_an_sfo_is_ignored(self):
        self.assertEqual(gui.parse_param_sfo(self._written(b"not an sfo")), {})

    def test_a_truncated_sfo_does_not_raise(self):
        raw = build_param_sfo([("APP_VER", "01.19")])
        for cut in range(1, len(raw)):
            with self.subTest(cut=cut):
                gui.parse_param_sfo(self._written(raw[:cut]))

    def test_found_beside_usrdir_and_inside_it(self):
        raw = build_param_sfo([("APP_VER", "01.19")])
        with tempfile.TemporaryDirectory() as root:
            usrdir = os.path.join(root, "USRDIR")
            os.makedirs(usrdir)
            self.assertEqual(gui.read_param_sfo(usrdir), {})
            with open(os.path.join(root, "PARAM.SFO"), "wb") as handle:
                handle.write(raw)
            self.assertEqual(gui.read_param_sfo(usrdir)["APP_VER"], "01.19")

    def test_missing_sfo_gives_no_update_rather_than_a_wrong_one(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(gui.read_param_sfo(root), {})
            self.assertEqual(gui.update_words(
                gui.read_param_sfo(root).get("APP_VER", "")), "")

    def _written(self, raw):
        handle = tempfile.NamedTemporaryFile(delete=False, suffix=".SFO")
        handle.write(raw)
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        return handle.name


FIXTURES = os.path.join(HERE, "test-fixtures")


def fixture(name):
    with open(os.path.join(FIXTURES, name), "r", encoding="utf-8") as handle:
        return handle.read()


class RealRetailHeaders(unittest.TestCase):
    """scetool -i output captured from stock retail files.

    These have an SCE Version block present, which the files this was first
    written against did not, and they put the NPDRM values inside the third
    Control Info block rather than under a heading of their own. An earlier
    parser looked for an "NPDRM Info" section that does not exist and dropped
    every NPDRM field, while still producing a dict that looked usable.
    """

    CASES = {
        "eboot-bles01717-stock.txt": ("BLES01717", "UEXEC"),
        "t6mp-bles01717-stock.txt": ("BLES01717", "USPRX"),
        "eboot-blus31140-stock.txt": ("BLUS31140", "UEXEC"),
    }

    def test_every_required_field_is_found(self):
        for name in self.CASES:
            with self.subTest(name=name):
                info = gui.parse_header(fixture(name))
                missing = [f for f in gui.REQUIRED_FIELDS if f not in info]
                self.assertEqual(missing, [])

    def test_title_and_app_type(self):
        for name, (title, app_type) in self.CASES.items():
            with self.subTest(name=name):
                info = gui.parse_header(fixture(name))
                self.assertEqual(
                    gui.title_id_from(info["content_id"]), title)
                self.assertEqual(info["app_type"], app_type)

    def test_npdrm_not_app(self):
        # The value reads "NPDRM Application", which contains both names. APP
        # would be signed without an NPDRM header and would not boot.
        for name in self.CASES:
            with self.subTest(name=name):
                self.assertEqual(
                    gui.parse_header(fixture(name))["self_type"], "NPDRM")

    def test_values_printed_as_numbers_rather_than_names(self):
        info = gui.parse_header(fixture("eboot-bles01717-stock.txt"))
        # Licence Type 0x00000003 and App Type 0x00000021, no bracket name.
        self.assertEqual(info["licence_type"], "FREE")
        self.assertEqual(info["app_type"], "UEXEC")
        # Auth-ID and Vendor-ID print names rather than values.
        self.assertEqual(info["auth_id"], "1010000001000003")
        self.assertEqual(info["vendor_id"], "01000002")

    def test_firmware_version_is_not_read_as_hex(self):
        # Printed as "FW Version 42000 [04.20]". Converting the 20 from base 16
        # signs the file as 4.32.
        info = gui.parse_header(fixture("eboot-bles01717-stock.txt"))
        self.assertEqual(info["fw_version"], "0004002000000000")
        self.assertEqual(gui._version_value("04.20"), "0004002000000000")

    def test_the_synthetic_header_still_parses(self):
        # The other layout, with no SCE Version block and bracketed names.
        info = gui.parse_header(header())
        self.assertEqual(info["self_type"], "NPDRM")
        self.assertEqual(info["app_type"], "UEXEC")
        self.assertEqual(info["licence_type"], "FREE")

    def test_a_header_missing_npdrm_values_is_not_silently_usable(self):
        trimmed = fixture("eboot-bles01717-stock.txt").split("Type      NPDRM")[0]
        info = gui.parse_header(trimmed)
        missing = [f for f in gui.REQUIRED_FIELDS if f not in info]
        self.assertIn("content_id", missing)
        self.assertIn("app_type", missing)


if __name__ == "__main__":
    unittest.main(verbosity=2)
