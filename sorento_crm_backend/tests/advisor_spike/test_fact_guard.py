"""Red tests for scripts.advisor_spike.fact_guard (CHAT-ADVISOR-SPIKE contract). Pure, no DB."""
from scripts.advisor_spike.fact_guard import GuardResult, check_reply

UUID = "123e4567-e89b-12d3-a456-426614174000"


def run(reply, evidence=(), users=()):
    return check_reply(reply, list(evidence), list(users))


def test_empty_reply_ok():
    r = run("", ["WC-2219 stock 5"])
    assert isinstance(r, GuardResult)
    assert r.ok is True and r.unsupported == [] and r.leaks == []


def test_reply_without_tokens_ok():
    assert run("Sure, happy to help.").ok is True


def test_code_supported():
    assert run("Product WC-2219 is available.", ["code: WC-2219"]).ok is True


def test_code_unsupported():
    r = run("Product WC-9999 is available.", ["code: WC-2219"])
    assert r.ok is False and r.unsupported == ["WC-9999"]


def test_code_case_insensitive():
    assert run("Try sb8812a today", ["SB8812A"]).ok is True
    assert run("Try SB8812A today", ["sb8812a"]).ok is True


def test_code_with_slash_and_dot_and_leading_digits():
    assert run("Code 2219-W ok", ["2219-W"]).ok is True
    r = run("Code 2219-X ok", ["2219-W"])
    assert r.unsupported == ["2219-X"]


def test_digits_inside_code_not_checked_separately():
    r = run("WC-2219", ["WC-2219"])
    assert r.ok is True and r.unsupported == []


def test_number_supported_by_value_thousands_and_decimals():
    ev = ["price 1250.00"]
    assert run("It costs 1,250", ev).ok is True
    assert run("It costs 1250", ev).ok is True
    assert run("It costs 1,250.00", ev).ok is True


def test_number_currency_prefix():
    assert run("Total RM 1,250.00", ["amount: 1250"]).ok is True
    assert run("Total RM1250", ["amount: 1250"]).ok is True


def test_number_unsupported():
    r = run("We have 77 units", ["stock 12"])
    assert r.ok is False and r.unsupported == ["77"]


def test_decimal_by_value():
    assert run("About 12.5 sqm", ["12.50"]).ok is True
    assert run("About 12.6 sqm", ["12.50"]).ok is False


def test_number_supported_from_user_text():
    assert run("You asked for 40 pcs.", [], ["I need 40 pcs please"]).ok is True


def test_code_supported_from_user_text():
    assert run("About WC-2219", [], ["tell me about wc-2219"]).ok is True


def test_list_enumerators_exempt():
    reply = "1. Alpha\n2) Beta\n3. Gamma"
    assert run(reply, []).ok is True


def test_enumerator_exempt_but_body_number_checked():
    r = run("1. Stock is 55", ["stock 12"])
    assert r.unsupported == ["55"]


def test_date_day_month_matches_iso():
    assert run("Arrives 12 Nov", ["eta 2026-11-12"]).ok is True


def test_date_with_year_matches_iso():
    assert run("Arrives 12 Nov 2026", ["eta 2026-11-12"]).ok is True


def test_date_iso_in_reply():
    assert run("ETA 2026-11-12", ["eta 2026-11-12"]).ok is True


def test_date_slash_dmy():
    assert run("ETA 12/11/2026", ["eta 2026-11-12"]).ok is True


def test_date_wrong_year_fails():
    r = run("Arrives 12 Nov 2027", ["eta 2026-11-12"])
    assert r.ok is False and len(r.unsupported) == 1


def test_date_wrong_day_fails():
    r = run("Arrives 13 Nov", ["eta 2026-11-12"])
    assert r.ok is False


def test_date_not_double_counted_as_numbers():
    r = run("Arrives 12 Nov 2026", ["eta 2026-11-12"])
    assert r.unsupported == []


def test_uuid_leak_even_when_in_evidence():
    r = run(f"Record {UUID} found", [f"id {UUID}"])
    assert r.ok is False and r.leaks == [UUID]


def test_uuid_digits_not_reported_unsupported_when_leak():
    r = run(f"Record {UUID}", [f"id {UUID}"])
    assert r.leaks == [UUID]


def test_unsupported_in_reply_order_and_deduped():
    r = run("Got 91 then ZZ-100 then 91 and 42", ["nothing relevant"])
    assert r.unsupported == ["91", "ZZ-100", "42"]


def test_ok_only_when_both_lists_empty():
    assert run("fine", []).ok is True
    assert run("99", []).ok is False
    assert run(UUID, [UUID]).ok is False
