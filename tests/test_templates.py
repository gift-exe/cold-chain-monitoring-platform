"""I5 acceptance (Table 3.2, NFR9, E4): every message template fits in one SMS of 160 characters of the
GSM 7-bit alphabet, even with the longest values it can be given; and reply commands parse as Table 3.19
specifies. No services needed.
"""
from datetime import UTC, datetime, timedelta

import pytest

from coldchain.notify import commands, templates
from coldchain.notify.codes import ALPHABET, LENGTH

NOW = datetime(2026, 11, 3, 9, 30, tzinfo=UTC)
LONG_PLACE = "Primary Health Care Centre Ọ̀yọ́-Ìbàdàn Road, Ungwan Rimi, Kaduna North walk-in cold room"
LONG_NAME = "Oluwaseyifunmi Adéṣọlá Babátúndé-Ọ̀ṣúnwálé"
CODE = ALPHABET[-LENGTH:]     # a code of the full length
NO = 9_999_999                # seven-digit incident and consignment numbers


def incident(type_="heat_excursion", severity="critical", place=LONG_PLACE, owner=LONG_NAME, peak=-12.5,
             acknowledged=False) -> templates.Incident:
    return templates.Incident(NO, type_, severity, place, peak, NOW - timedelta(days=12), None, owner, acknowledged)


def all_messages() -> list[str]:
    out = []
    for type_ in templates.TYPES:
        for severity in ("critical", "warning"):
            for ack in (False, True):
                i = incident(type_, severity, acknowledged=ack)
                out += [templates.alert(i, CODE, NOW), templates.cc(i, NOW),
                        templates.escalation(i, CODE, NOW), templates.escalation(i, CODE, NOW, undelivered=True),
                        templates.overdue(i, CODE, NOW), templates.overdue(i, CODE, NOW, reminder=True),
                        templates.reopened(i, CODE, NOW)]
    out += [templates.digest(NOW, list(range(NO - n, NO))) for n in (1, 5, 50)]
    out += [templates.handoff(NO, LONG_NAME, 1000, "Pentavalent (DTP-HepB-Hib) and Ọ̀ṣun extra", CODE),
            templates.handoff_undelivered(NO, LONG_NAME),
            templates.admin_rejected(LONG_NAME, "+234800000000099", "InvalidPhoneNumber: " + "x" * 80),
            templates.HELP]
    return out


def test_every_template_fits_one_gsm_sms():
    for text in all_messages():
        assert len(text) <= templates.LIMIT, (len(text), text)
        assert templates.fits(text), text


def test_alert_matches_the_example_in_section_3_4_6():
    i = templates.Incident(4821, "freeze", "critical", "Gwale LGA store Fridge 2", -1.4, NOW - timedelta(minutes=12),
                           None, "A. Bello", False)
    assert templates.alert(i, "K7QM", NOW) == ("CRIT FREEZE Gwale LGA store Fridge 2: -1.4C for 12 min. "
                                               "You own #4821. Reply ACK 4821. When fixed: RES 4821 K7QM note")


def test_long_names_are_shortened_but_numbers_codes_and_commands_never_are():
    text = templates.alert(incident(), CODE, NOW)
    assert text.endswith(f"You own #{NO}. Reply ACK {NO}. When fixed: RES {NO} {CODE} note")
    assert "Primary Health" in text and len(text) == templates.LIMIT


def test_names_are_transliterated_to_the_gsm_alphabet():
    assert templates.gsm("Ọ̀yọ́ Ìbàdàn") == "Oyo Ibadan"
    assert templates.gsm("Ɓauchi 4°C") == "?auchi 4?C"     # hooked B and the degree sign are not GSM
    assert templates.name("Amina Bello Yusuf") == "A. Yusuf"


def test_digest_lists_numbers_and_counts_the_rest():
    text = templates.digest(NOW, list(range(1000, 1050)))
    assert text.startswith("RETRO 03 Nov: 50 past incidents") and "#1000" in text and text.endswith("more")
    shown = text.count("#")
    assert text.endswith(f"+{50 - shown} more")
    assert templates.digest(NOW, [7]) == "RETRO 03 Nov: 1 past incident to review on the dashboard: #7"


# Reply commands ----------------------------------------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("ACK 4821", ("ACK", ("4821",))),
    ("  ack   #4821 ", ("ACK", ("4821",))),
    ("RES 4821 k7qm moved to backup fridge", ("RES", ("4821", "k7qm", "moved to backup fridge"))),
    ("RES 4821 K7QM", ("RES", ("4821", "K7QM", None))),
    ("REC 312 K7QM", ("REC", ("312", "K7QM"))),
    ("Rej 312 K7QM carrier warm on arrival", ("REJ", ("312", "K7QM", "carrier warm on arrival"))),
    ("help", ("HELP", ())),
])
def test_commands_parse(text, expected):
    assert commands.parse(text) == expected


@pytest.mark.parametrize("text, reply", [
    ("ACK", "Use: ACK <incident>"),
    ("ACK forty", "Use: ACK <incident>"),
    ("RES 4821", "Use: RES <incident> <code> <note>"),
    ("CLOSE 4821", "dashboard"),
    ("Thanks!", "Send HELP"),
    ("", "Send HELP"),
])
def test_malformed_commands_rejected_with_a_hint(text, reply):
    with pytest.raises(commands.Rejected, match=reply):
        commands.parse(text)


def test_codes_are_masked_before_storing():
    assert commands.mask("RES 4821 K7QM fixed it", "RES") == "RES 4821 **** fixed it"
    assert commands.mask("REC 312 K7QM", "REC") == "REC 312 ****"
    assert commands.mask("ACK 4821", "ACK") == "ACK 4821"
