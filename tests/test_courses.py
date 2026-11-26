"""Tests for dtumcp.courses."""
import os
import time

from _package import mod


def test_flatten():
    toc = [
        {
            "ModuleId": 1,
            "Title": "Week 1",
            "Description": {"Text": "Tuesday 1/09", "Html": ""},
            "Topics": [
                {"TopicId": 11, "Title": "Slides (2026)", "TypeIdentifier": "File"},
                # Copied from last year without its file: counted, not listed.
                {"TopicId": 10, "Title": "Slides (2025)", "TypeIdentifier": "File", "IsBroken": True},
            ],
            "Modules": [
                {
                    "ModuleId": 2,
                    "Title": "Exercises",
                    "Topics": [
                        # An internal quicklink is relative and stays hidden.
                        {
                            "TopicId": 12,
                            "Title": "Sheet",
                            "TypeIdentifier": "Link",
                            "Url": "/d2l/common/dialogs/quickLink/quickLink.d2l?ou=1",
                        },
                        # A link to the course's own site is public and worth showing.
                        {
                            "TopicId": 13,
                            "Title": "Notes",
                            "TypeIdentifier": "Link",
                            "Url": "https://courses.compute.dtu.dk/02242/",
                        },
                    ],
                }
            ],
        },
        {"ModuleId": 3, "Title": "Empty"},
    ]
    assert mod._flatten(toc) == [
        "# [1] Week 1 (info) [1 broken topic not listed]",
        "  - [11] Slides (2026) (File)",
        "  # [2] Exercises",
        "    - [12] Sheet (Link)",
        "    - [13] Notes (Link) https://courses.compute.dtu.dk/02242/",
        "# [3] Empty",
    ], mod._flatten(toc)
    assert mod._flatten(None) == []

    # A unit's own text prefers the plain rendition and falls back to the HTML one.
    assert mod._description({"Description": {"Text": " Tuesday ", "Html": "<p>x</p>"}}) == "Tuesday"
    assert mod._description({"Description": {"Text": "", "Html": "<p>Week <b>1</b></p>"}}) == "Week 1"
    assert mod._description({}) == ""


def test_urn_kind():
    assert (
        mod._urn_kind(
            "d2l:brightspace:content:eu-west-1:c58c6536:video:1d2f9826/latest"
        )
        == "video"
    )
    assert mod._urn_kind("https://example.com/thing") == "media item"
    assert mod._urn_kind("") == "media item"


def test_term():
    def term(name, code=""):
        return mod._term({"Name": name, "Code": code})


    # The name wins whenever it carries a term, in any of the spellings DTU uses.
    assert term("02249 Computationally Hard Problems, Fall 2026") == (2026, 3)
    assert term("02157 Functional Programming Fall 23") == (2023, 3)
    assert term("47203 Design-Build 3: solar cooker Jan 24") == (2024, 0)
    assert term("27016 Devices for measuring cell growth, January 2025") == (2025, 0)
    assert term("10240 Design-Build 2: catalyst design Jun 23") == (2023, 2)
    assert term("42620 Science, technology and society, Summer 2024") == (2024, 2)
    assert term("02135 Introduction to Cyber Systems Spring 23") == (2023, 1)
    assert term("TA Education (f2f), 31st of August - Fall 2023") == (2023, 3)

    # A course spanning two terms is filed under the one it starts in.
    assert term("01006 Advanced Engineering Mathematics 1 Fall22-Spring23") == (2022, 3)

    # With no term in the name, fall back to the offering code.
    assert term("02158 Concurrent Programming E24", "DTU_e24_02158") == (2024, 3)
    assert term("02105 Algorithms 1", "DTU_f24_02105") == (2024, 1)
    assert term("22400 Design-build 4", "DTU_s24_22400") == (2024, 2)

    # The code's year is the academic year, whose January is the next calendar year.
    assert term("27016 Devices", "DTU_v25_27016") == (2026, 0)

    # Neither source has a term.
    assert term("Module 0 - Your Digital Platforms at DTU", "OFFERING-132988") == (0, -1)
    assert term("01006 Adv. Eng. Math. 1/GENENG 3", "OFFERING-666591") == (0, -1)

    # A bare number after a season word is not a year.
    assert term("Spring 5 Framework Basics") == (0, -1)

    # Newest term sorts first, and within a year the latest season leads.
    assert sorted([(2025, 1), (2026, 3), (0, -1), (2026, 0)], reverse=True) == [
        (2026, 3),
        (2026, 0),
        (2025, 1),
        (0, -1),
    ]


def test_calendar_lines():
    os.environ["TZ"] = "Europe/Copenhagen"
    time.tzset()  # the tool prints local time, so pin the zone the events were booked in

    events = [
        # Out of order on purpose: the day headings depend on sorting them first.
        {"StartDateTime": "2026-09-21T19:00:00.000Z", "EndDateTime": "2026-09-21T19:00:00.000Z",
         "IsAllDayEvent": False, "Title": "Optional Hand-in Exercise 3", "LocationName": "",
         "OrgUnitName": "02249 Computationally Hard Problems, Fall 2026",
         "AssociatedEntity": {"AssociatedEntityType": "D2L.LE.Dropbox.Dropbox"}},
        {"StartDateTime": "2026-09-21T06:00:00.000Z", "EndDateTime": "2026-09-21T08:15:00.000Z",
         "IsAllDayEvent": False, "Title": "Lecture", "LocationName": "LYN.306.AUD35.R:135",
         "OrgUnitName": "02231 Cryptography fundamentals, Fall 2026", "AssociatedEntity": None},
        {"StartDateTime": "2026-09-22T06:00:00.000Z", "EndDateTime": "2026-09-22T06:00:00.000Z",
         "IsAllDayEvent": True, "Title": "Project week", "LocationName": " ".join("room" for _ in range(20)),
         "OrgUnitName": "Study line", "AssociatedEntity": None},
    ]
    assert mod._calendar_lines(events) == [
        "\n## Mon 21 Sep 2026",
        "08:00-10:15  02231  Lecture — LYN.306.AUD35.R:135",
        "21:00  02249  due: Optional Hand-in Exercise 3",
        "\n## Tue 22 Sep 2026",
        "all day  Study line  Project week — " + "room " * 11 + "ro...",
    ], mod._calendar_lines(events)


def test_resolve_for():
    # A course number's offering is kept RESOLVE_FOR seconds, so a new term's
    # offering of the number shows up once that runs out.
    saved_enrolled = mod._enrolled
    offerings = [{"Id": 326352, "Name": "02231 Cryptography, Fall 2026"}]
    mod._enrolled = lambda: (offerings, "")
    mod._resolved.clear()
    assert mod._resolve("02231") == 326352
    offerings.append({"Id": 340001, "Name": "02231 Cryptography, Spring 2027"})
    assert mod._resolve(2231) == 326352  # still kept
    mod._resolved[2231] = (326352, mod.time.monotonic() - mod.RESOLVE_FOR)
    try:
        mod._resolve("02231")
        raise AssertionError("two offerings should be named")
    except RuntimeError as e:
        assert "several offerings" in str(e) and "340001" in str(e), e
    mod._enrolled = saved_enrolled
    mod._resolved.clear()


def test_groups():
    # The Groups tab: the learner's own group in each category, its members named
    # from the class list (whose ids are strings), a category without one marked so.
    categories = [
        {"Name": "Project", "EnrollmentStyle": 5, "MaxUsersPerGroup": 3,
         "SelfEnrollmentExpiryDate": "2026-10-06T07:00:00.000Z", "Description": {"Text": "Pick one", "Html": "<p>Pick one</p>"}},
        {"Name": "Feedback", "EnrollmentStyle": 1},
    ]
    groups = [
        [{"Name": "project 1", "Enrollments": [5, 6]}, {"Name": "project 2", "Enrollments": [7, 42, 99]}],
        [{"Name": "feedback 1", "Enrollments": [5]}],
    ]
    classlist = [{"Identifier": "42", "DisplayName": "Me", "Email": "me@dtu.dk", "ClasslistRoleDisplayName": "Student"},
                 {"Identifier": "7", "FirstName": "Ada", "LastName": "L", "Email": "ada@dtu.dk", "ClasslistRoleDisplayName": "Student"}]
    rows = mod._group_rows(categories, groups, 42, classlist)
    assert [r["groups"] for r in rows] == [2, 1] and [r["signup"] for r in rows] == [True, False], rows
    assert rows[0]["mine"]["name"] == "project 2" and rows[1]["mine"] is None, rows
    # 99 left the course: not in the class list, so not named.
    assert [(m["name"], m["me"]) for m in rows[0]["mine"]["members"]] == [("Ada L", False), ("Me", True)], rows[0]
    assert rows[0]["description"] == {"text": "Pick one", "html": "<p>Pick one</p>"}
    assert mod._rich(None) == {"text": "", "html": ""}
