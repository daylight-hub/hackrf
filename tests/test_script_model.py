import textwrap

import pytest

from rfstudio import script_model as sm

SCRIPT = textwrap.dedent('''\
    """Doc string."""
    import os

    TOP_LEVEL = 1  # before any header

    # ===== Radio Settings =====
    ENABLE_TX = False   # Transmit enable
    FREQ = 433.92 * 1e6  # @unit Hz Centre frequency
    # The gain applied before the ADC
    LNA_GAIN = 16  # @range 0..40 @step 8
    MODE = "fm"  # @options fm|am|usb Demodulator
    NEG = -3.5

    # [Output]
    VERBOSE: bool = True
    PATH = "out.bin"
    TAGS = ["a",
            "b"]  # list literal
    SECRET = "x"  # @hide
    SKIPPED = 2  # @ignore
    _private = 3
    computed = os.getcwd()
    A = B = 4

    # LOGGING
    LEVEL = 2

    def f():
        INNER = True
''')


@pytest.fixture()
def spec(tmp_path):
    p = tmp_path / "s.py"
    p.write_text(SCRIPT)
    return sm.parse_script(p)


def test_discovers_literals_only(spec):
    names = [v.name for v in spec.variables]
    assert names == ["TOP_LEVEL", "ENABLE_TX", "FREQ", "LNA_GAIN", "MODE", "NEG",
                     "VERBOSE", "PATH", "TAGS", "SECRET", "LEVEL"]
    assert spec.docstring == "Doc string."


def test_sections(spec):
    by = spec.by_name()
    assert by["TOP_LEVEL"].section == sm.DEFAULT_SECTION
    assert by["ENABLE_TX"].section == "Radio Settings"
    assert by["VERBOSE"].section == "Output"
    assert by["LEVEL"].section == "Logging"
    assert spec.sections == [sm.DEFAULT_SECTION, "Radio Settings", "Output", "Logging"]


def test_kinds_and_hints(spec):
    by = spec.by_name()
    assert by["ENABLE_TX"].kind == sm.KIND_BOOL and by["ENABLE_TX"].description == "Transmit enable"
    assert by["FREQ"].kind == sm.KIND_FLOAT and by["FREQ"].default == pytest.approx(433.92e6)
    assert by["FREQ"].unit == "Hz" and by["FREQ"].description == "Centre frequency"
    assert by["LNA_GAIN"].minimum == 0 and by["LNA_GAIN"].maximum == 40 and by["LNA_GAIN"].step == 8
    assert by["LNA_GAIN"].description == "The gain applied before the ADC"
    assert by["MODE"].kind == sm.KIND_CHOICE and by["MODE"].choices == ["fm", "am", "usb"]
    assert by["NEG"].default == -3.5
    assert by["VERBOSE"].kind == sm.KIND_BOOL
    assert by["TAGS"].kind == sm.KIND_LITERAL
    assert by["SECRET"].hidden


def test_coerce():
    v = sm.ScriptVariable("F", sm.KIND_FLOAT, 1.0, minimum=0, maximum=10e9)
    assert v.coerce("433.92M") == pytest.approx(433.92e6)
    with pytest.raises(ValueError):
        v.coerce("-5")
    i = sm.ScriptVariable("I", sm.KIND_INT, 1)
    assert i.coerce("2k") == 2000
    with pytest.raises(ValueError):
        i.coerce("1.5")
    b = sm.ScriptVariable("B", sm.KIND_BOOL, False)
    assert b.coerce("true") is True


def test_parse_number_text():
    assert sm.parse_number_text("2_000_000") == 2000000
    assert sm.parse_number_text("8e6") == 8e6
    assert sm.parse_number_text("1.5GHz") == 1.5e9
    assert sm.parse_number_text("0x10") == 16
    with pytest.raises(ValueError):
        sm.parse_number_text("abc")


def test_apply_overrides_preserves_lines(spec):
    patched, warnings = sm.apply_overrides(SCRIPT, spec, {
        "ENABLE_TX": True, "FREQ": 915e6, "TAGS": ["x"], "MODE": "am", "GONE": 1, "LNA_GAIN": 99,
    })
    assert len(patched.splitlines()) == len(SCRIPT.splitlines())
    ns: dict = {}
    exec(compile(patched.replace("import os", "import os as os"), "p", "exec"), ns)
    assert ns["ENABLE_TX"] is True
    assert ns["FREQ"] == 915e6
    assert ns["TAGS"] == ["x"]
    assert ns["MODE"] == "am"
    assert ns["LNA_GAIN"] == 16  # out of range -> default kept
    assert any("GONE" in w for w in warnings)
    assert any("LNA_GAIN" in w for w in warnings)
    assert "# Transmit enable" in patched  # comments untouched


def test_unicode_offsets(tmp_path):
    src = 'NAME = "héllo wörld"  # ünïcode\nX = 1\n'
    spec = sm.parse_script(tmp_path / "u.py", src)
    patched, _ = sm.apply_overrides(src, spec, {"NAME": "ok", "X": 2})
    assert patched == 'NAME = \'ok\'  # ünïcode\nX = 2\n'


def test_gui_markers(tmp_path):
    src = "A = 1\n# @gui-begin\nB = 2\n# @gui-end\nC = 3\n"
    spec = sm.parse_script(tmp_path / "m.py", src)
    assert [v.name for v in spec.variables] == ["B"]


def test_syntax_error(tmp_path):
    spec = sm.parse_script(tmp_path / "bad.py", "x = (\n")
    assert spec.errors and not spec.variables


def test_humanize():
    assert sm.humanize("ENABLE_TX") == "Enable TX"
    assert sm.humanize("lnaGain") == "LNA gain"
    assert sm.humanize("CENTER_FREQ") == "Center freq"
