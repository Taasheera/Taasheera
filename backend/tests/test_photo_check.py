from io import BytesIO

import pytest
from PIL import Image, ImageDraw, ImageFilter
from sqlmodel import Session, select

from app.applications import flow
from app.documents.photo import check_photo, load_specs, photo_spec
from app.models import DocumentAccess
from test_applications_api import answer_all, api, start  # noqa: F401
from test_supporting_documents import consent_not_required, make_pdf, no_ocr, upload  # noqa: F401


def portrait(size=(600, 600), background=(245, 245, 245), grey=False, blur=0, stripes=False, format="JPEG"):
    width, height = size
    image = Image.new("RGB", size, background)
    draw = ImageDraw.Draw(image)
    if stripes:
        for x in range(0, width, 24):
            draw.rectangle((x, 0, x + 11, height), fill=(30, 90, 160))
    draw.rectangle((width * 0.15, height * 0.78, width * 0.85, height), fill=(30, 40, 90))
    draw.ellipse((width * 0.3, height * 0.18, width * 0.7, height * 0.75), fill=(214, 160, 120))
    draw.ellipse((width * 0.28, height * 0.12, width * 0.72, height * 0.35), fill=(60, 40, 30))
    for x in (0.4, 0.56):
        draw.ellipse((width * x, height * 0.42, width * (x + 0.05), height * 0.46), fill=(30, 30, 30))
    draw.line((width * 0.43, height * 0.62, width * 0.57, height * 0.62), fill=(140, 60, 60), width=4)
    if grey:
        image = image.convert("L").convert("RGB")
    if blur:
        image = image.filter(ImageFilter.GaussianBlur(blur))
    out = BytesIO()
    image.save(out, format=format, quality=90)
    return out.getvalue()


def failed(report):
    return [check.id for check in report.checks if not check.passed]


US = photo_spec("us-b2-tourist", "photo")


def test_every_photo_requirement_has_a_spec_with_an_official_source():
    specs = {(spec.questionnaire_id, spec.requirement) for spec in load_specs().specs}
    asked = {(q.id, d.id) for q in flow.available_questionnaires() for d in q.documents if d.id == "photo"}
    assert specs == asked
    assert all(spec.source.url.startswith("https://") for spec in load_specs().specs)


def test_a_good_photo_passes_every_check():
    report = check_photo(portrait(), "image/jpeg", US)
    assert report.passed and failed(report) == []
    assert [check.id for check in report.checks] == [
        "size", "max_size", "shape", "colour", "background", "sharpness", "file_size"]
    assert all(check.guidance is None for check in report.checks)
    assert "don't recognise faces" in report.notice and "not legal" in report.notice
    assert report.source.url.startswith("https://travel.state.gov/")


@pytest.mark.parametrize("drawn, questionnaire_id, expected", [
    ({"size": (599, 599)}, "us-b2-tourist", ["size"]),
    ({"size": (1300, 1300)}, "us-b2-tourist", ["max_size"]),
    ({"size": (600, 750)}, "us-b2-tourist", ["shape"]),
    ({"grey": True}, "us-b2-tourist", ["colour"]),
    ({"background": (60, 60, 60)}, "us-b2-tourist", ["background"]),
    ({"background": (185, 185, 185)}, "us-b2-tourist", ["background"]),
    ({"stripes": True}, "us-b2-tourist", ["background"]),
    ({"blur": 6}, "us-b2-tourist", ["sharpness"]),
    ({"size": (413, 531)}, "schengen-short-stay", []),
    ({"size": (413, 531), "background": (185, 185, 185)}, "schengen-short-stay", []),
    ({"size": (354, 472)}, "china-tourist-l", ["file_size"]),
    ({"size": (413, 531), "grey": True}, "canada-visitor", []),
])
def test_each_failure_names_what_is_wrong_and_how_to_retake_it(drawn, questionnaire_id, expected):
    report = check_photo(portrait(**drawn), "image/jpeg", photo_spec(questionnaire_id, "photo"))
    assert failed(report) == expected and report.passed is (expected == [])
    for check in report.checks:
        assert (check.guidance is None) is check.passed


def test_shape_allows_a_small_crop_tolerance():
    assert "shape" not in failed(check_photo(portrait((600, 612)), "image/jpeg", US))
    assert "shape" in failed(check_photo(portrait((600, 630)), "image/jpeg", US))


def test_pdfs_and_unreadable_files_are_not_checked():
    [check] = check_photo(b"%PDF-1.4", "application/pdf", US).checks
    assert check.id == "file_type" and not check.passed and "JPEG" in check.guidance
    [check] = check_photo(b"not an image", "image/jpeg", US).checks
    assert check.id == "file_type" and "couldn't be opened" in check.message


def test_messages_hold_measurements_not_file_contents():
    report = check_photo(portrait((500, 500)), "image/jpeg", US)
    size = next(check for check in report.checks if check.id == "size")
    assert size.message == "It is 500 x 500 pixels; at least 600 x 600 are needed."
    assert "full resolution" in size.guidance


def test_the_photo_check_route_is_owner_only_and_audited(api):
    client, app, headers, _ = api
    application = start(client, headers[0])
    photo = upload(client, headers[0], application["id"], "photo", portrait((413, 531), format="PNG"), "me.png").json()
    url = f"/applications/{application['id']}/documents/{photo['id']}/photo-check"
    response = client.get(url, headers=headers[0])
    assert response.status_code == 200 and response.headers["cache-control"] == "private, no-store"
    report = response.json()
    assert report["passed"] is True and report["spec"].startswith("Schengen visa photo")
    assert client.get(url, headers=headers[1]).status_code == 404
    with Session(app.state.engine) as session:
        assert {row.purpose for row in session.exec(select(DocumentAccess))} == {"document_check"}

    statement = upload(client, headers[0], application["id"], "bank_statements", make_pdf("Statement")).json()
    response = client.get(f"/applications/{application['id']}/documents/{statement['id']}/photo-check", headers=headers[0])
    assert response.status_code == 404 and "isn't a photo" in response.json()["detail"]


def test_a_failing_photo_is_a_warning_in_the_completeness_check(api, monkeypatch):
    client, app, headers, _ = api
    application = start(client, headers[0])
    answer_all(client, headers[0], application["id"])
    upload(client, headers[0], application["id"], "photo", portrait((300, 300), grey=True), "small.jpg")
    issues = client.get(f"/applications/{application['id']}/check", headers=headers[0]).json()["issues"]
    [warning] = [issue for issue in issues if issue["document_id"] == "photo"]
    assert warning["severity"] == "warning" and warning["kind"] == "inconsistent"
    assert "small.jpg" in warning["message"] and "black and white" in warning["message"]
    assert warning["source"].startswith("https://")

    flags = type(app.state.feature_flags)
    monkeypatch.setattr(app.state, "feature_flags", flags(frozenset({"supporting_documents"})))
    issues = client.get(f"/applications/{application['id']}/check", headers=headers[0]).json()["issues"]
    assert not [issue for issue in issues if issue["document_id"] == "photo"]
    photo_id = client.get(f"/applications/{application['id']}/documents", headers=headers[0]).json()[
        "requirements"][0]["documents"][0]["id"]
    assert client.get(f"/applications/{application['id']}/documents/{photo_id}/photo-check",
                      headers=headers[0]).status_code == 404
