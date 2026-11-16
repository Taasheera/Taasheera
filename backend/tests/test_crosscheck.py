"""The document cross-check on its own (CMPS271-50): no database, no HTTP, synthetic data only."""

import pytest

from app.documents.crosscheck import DocumentText, cross_check, fold

# ICAO 9303 specimen passport ("Utopia"). Never use real passport data in tests.
PASSPORT = {"surname": "ERIKSSON", "given_names": "ANNA MARIA", "document_number": "L898902C3",
            "birth_date": "1974-08-12", "nationality": "UTO"}
PADDING = " Booking reference EXAMPLE123. Thank you for choosing Example Travel. "


def document(text, requirement="flight_booking", label="Flight reservation", filename="booking.pdf", id="a" * 32):
    return DocumentText(id=id, requirement=requirement, label=label, filename=filename, text=text + PADDING)


def findings(*documents, **sources):
    return cross_check(PASSPORT, documents=list(documents), **sources).findings


def test_matching_documents_have_no_findings():
    report = cross_check(PASSPORT, latest_passport=dict(PASSPORT), profile={**PASSPORT, "phone": "+96171123456"},
                         documents=[document("Passenger: Ms Anna Maria Eriksson, passport no L898902C3, "
                                             "date of birth 12/08/1974")])
    assert report.findings == []
    assert report.reference == "application"
    assert report.checked == ["Your confirmed passport", "Your profile", "Flight reservation (booking.pdf)"]


def test_a_misspelt_surname_is_found_with_where_it_was_found():
    [finding] = findings(document("Guest: ANNA ERICSSON", requirement="accommodation_proof",
                                  label="Accommodation booking", filename="hotel.pdf"))
    assert (finding.field, finding.source, finding.expected, finding.found) == ("name", "document", "ERIKSSON", "ERICSSON")
    assert finding.where == "Accommodation booking (hotel.pdf)" and finding.requirement == "accommodation_proof"
    assert "spells your surname ERICSSON" in finding.message


def test_a_missing_surname_is_reported():
    [finding] = findings(document("Account holder: John Example"))
    assert finding.field == "name" and finding.found == "" and "couldn't find your surname" in finding.message


def test_a_misspelt_first_name_is_reported():
    [finding] = findings(document("Traveller: ANA ERIKSSON"))
    assert (finding.expected, finding.found) == ("ANNA", "ANA")


def test_accents_and_case_do_not_count_as_differences():
    assert findings(document("Passenger: anna Erikssón")) == []
    assert fold("Érîksson, Anna") == "ERIKSSON ANNA"


def test_a_different_passport_number_is_reported():
    [finding] = findings(document("Anna Eriksson Passport Number: X1234567"))
    assert (finding.field, finding.expected, finding.found) == ("document_number", "L898902C3", "X1234567")


def test_words_after_passport_that_are_not_numbers_are_ignored():
    assert findings(document("Anna Eriksson must carry her passport holder and a PASSPORT copy")) == []


@pytest.mark.parametrize("written", ["DOB: 1974-08-21", "Date of birth 21.08.1974", "born on 21 August 1974",
                                     "Birth date: 21/08/1974", "D.O.B 21 AUG 1974"])
def test_a_different_date_of_birth_is_reported(written):
    [finding] = findings(document(f"Anna Eriksson {written}"))
    assert (finding.field, finding.expected, finding.found) == ("birth_date", "1974-08-12", "1974-08-21")


def test_the_same_date_of_birth_in_any_format_matches():
    for written in ("DOB: 1974-08-12", "Date of birth 12.08.1974", "born on 12 Aug 1974"):
        assert findings(document(f"Anna Eriksson {written}")) == []


def test_unreadable_documents_are_listed_not_passed():
    report = cross_check(PASSPORT, documents=[DocumentText(id="b" * 32, requirement="photo", label="Passport photo",
                                                           filename="me.jpg", text="  ")])
    assert report.unreadable == ["Passport photo (me.jpg)"] and report.checked == [] and report.findings == []


def test_profile_and_confirmed_passport_are_compared_too():
    report = cross_check(PASSPORT, latest_passport={**PASSPORT, "document_number": "N1111111"},
                         profile={"surname": "ERIKSON", "given_names": "ANNA MARIA", "birth_date": "1974-08-13"})
    found = {(f.source, f.field): f for f in report.findings}
    assert set(found) == {("passport", "document_number"), ("profile", "name"), ("profile", "birth_date")}
    assert found["profile", "name"].where == "Your profile"
    assert found["profile", "name"].found == "ANNA MARIA ERIKSON"


def test_without_a_passport_in_the_application_the_confirmed_passport_is_the_reference():
    report = cross_check(None, latest_passport=PASSPORT, documents=[document("Guest: ANNA ERICSSON")])
    assert report.reference == "passport" and [f.found for f in report.findings] == ["ERICSSON"]


def test_without_any_passport_there_is_nothing_to_compare():
    report = cross_check(None, profile=PASSPORT, documents=[document("Guest: ANNA ERICSSON")])
    assert report.reference is None and report.findings == [] and report.checked == []
