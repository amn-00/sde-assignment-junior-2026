import pytest

from ledger.normalize import (
    normalize_header, normalize_phone, parse_date, parse_decimal, parse_enum, parse_timestamp,
    to_epoch_us, ist_iso, ist_date,
)


@pytest.mark.parametrize("raw", [
    "9876543210", "+91 98765 43210", "09876543210", "91-98765-43210", "(+91) 9876543210",
    "+919876543210", " 98765-43210 ",
])
def test_phone_all_formats_normalise(raw):
    assert normalize_phone(raw) == "+919876543210"


@pytest.mark.parametrize("raw", [
    "987654321",          # 9 digits
    "5876543210",         # bad first digit
    "022-2345-6789",      # landline (11 digits with 0 -> starts with 2)
    "N/A", "", None,
    "+1 415 555 0134",    # US number
    "919876543",          # starts with 91 but not 12 digits
    "00919876543210",     # 14 digits
])
def test_phone_invalid(raw):
    assert normalize_phone(raw) is None


@pytest.mark.parametrize("raw,expected", [
    ("2026-12-01", "2026-12-01"),
    ("01/12/2026", "2026-12-01"),      # day-first
    ("01-12-2026", "2026-12-01"),
    ("01-Dec-2026", "2026-12-01"),
    ("01-dec-2026", "2026-12-01"),     # month name is case-insensitive
    ("29/02/2028", "2028-02-29"),      # leap year
])
def test_date_valid(raw, expected):
    assert parse_date(raw) == expected


@pytest.mark.parametrize("raw", ["31/02/2026", "2026-13-01", "soon", "TBD", "12/31/2026",
                                 "01-Foo-2026", "29/02/2026"])
def test_date_invalid(raw):
    assert parse_date(raw) is None


@pytest.mark.parametrize("raw,expected", [
    ("27554", "27554.00"), ("₹27,554", "27554.00"), ("Rs. 500", "500.00"), ("rs500", "500.00"),
    ("Rs 1,200.5", "1200.50"), ("INR 25301.4", "25301.40"), ("inr12.345", "12.35"), ("0", "0.00"),
])
def test_decimal_valid(raw, expected):
    assert parse_decimal(raw) == expected


@pytest.mark.parametrize("raw", ["N/A", "-", "twelve thousand", "-500", "Rs.", "$500"])
def test_decimal_invalid(raw):
    assert parse_decimal(raw) is None


def test_enum_case_insensitive_and_lowercased():
    langs = ["en", "hi"]
    assert parse_enum("EN", langs) == "en"
    assert parse_enum(" hi ", langs) == "hi"
    assert parse_enum("Hindi", langs) is None


@pytest.mark.parametrize("raw,expected", [
    ("Mobile No", "mobile no"), ("  customer__name ", "customer name"),
    ("Policy_ No", "policy no"), ("agent_notes", "agent notes"), ("phone_", "phone"),
])
def test_header_normalisation(raw, expected):
    assert normalize_header(raw) == expected


def test_timestamps_utc_offset_and_naive_ist_are_the_same_instant():
    a = parse_timestamp("2026-10-03T04:31:00Z")
    b = parse_timestamp("2026-10-03T10:01:00+05:30")
    c = parse_timestamp("2026-10-03 10:01:00")  # naive => IST
    assert to_epoch_us(a) == to_epoch_us(b) == to_epoch_us(c)
    assert ist_iso(to_epoch_us(a)) == "2026-10-03T10:01:00+05:30"


def test_ist_date_crosses_midnight():
    # 20:00 UTC is 01:30 IST the next day
    assert ist_date(to_epoch_us(parse_timestamp("2026-10-03T20:00:00Z"))) == "2026-10-04"


def test_bad_timestamp():
    assert parse_timestamp("yesterday") is None
    assert parse_timestamp(None) is None
