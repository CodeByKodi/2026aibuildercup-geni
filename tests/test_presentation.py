from workpilot import presentation
from workpilot.models import ActionStatus


def test_session_actions_do_not_dump_workspace_without_a_decision():
    leftover = {"action_id": "ACT-old", "decision_id": "DEC-old", "title": "Prior"}
    assert presentation.session_actions([leftover], decision_id="") == []
    assert presentation.session_actions([leftover], decision_id="DEC-new") == []


def test_evidence_cards_include_document_section_and_snippet():
    cards = presentation.evidence_cards(
        [{"source": "security_assessment.md", "text": "SEC-217 token lifetime", "page": 2}]
    )
    assert cards[0]["source"] == "security_assessment.md"
    assert cards[0]["locator"] == "section 2"
    assert "SEC-217" in cards[0]["excerpt"]


def test_approval_and_execution_labels_stay_separate():
    action = {"action_id": "ACT-1", "status": ActionStatus.APPROVED, "execution_status": "NOT_STARTED"}
    assert presentation.approval_label(action["status"]) == "Approved"
    assert presentation.execution_label(action) == "Not started"
    assert presentation.approval_label(ActionStatus.REJECTED) == "Rejected"
    assert presentation.execution_label({**action, "execution_status": "EXECUTED"}) == "Executed"
    assert presentation.execution_label({**action, "execution_status": "SUCCESS"}) == "Not started"
    assert presentation.execution_label({**action, "execution_status": "FAILED"}) == "Failed"


def test_verdict_banner_counts_open_conditions():
    decision = {"readiness_status": "NOT READY", "approval_conditions": ["SEC-217", "PAY-482", "p95 < 1.5s"]}
    v = presentation.verdict(decision)
    assert v["headline"] == "NOT READY — 3 of 3 approval conditions open" and v["tone"] == "red"
    board = [
        {"status": ActionStatus.VERIFIED, "outcome_status": "ACHIEVED"},
        {"status": ActionStatus.APPROVED, "outcome_status": "PENDING"},
        {"status": ActionStatus.PROPOSED, "outcome_status": "PENDING"},
    ]
    v = presentation.verdict(decision, board)
    assert v["headline"] == "AT RISK — 2 of 3 approval conditions open" and v["tone"] == "amber"
    # Fewer contracts than conditions: the denominator does not shrink between Decide and Measure.
    one = [{"status": ActionStatus.APPROVED, "outcome_status": "PENDING"}]
    assert presentation.verdict(decision, one)["headline"] == "NOT READY — 3 of 3 approval conditions open"
    one_done = [{"status": ActionStatus.VERIFIED, "outcome_status": "ACHIEVED"}]
    assert presentation.verdict(decision, one_done)["headline"] == "AT RISK — 2 of 3 approval conditions open"
    # More contracts than conditions: an open contract keeps the release from looking ready.
    four = [{"status": ActionStatus.VERIFIED, "outcome_status": "ACHIEVED"}] * 3 + [{"status": ActionStatus.APPROVED}]
    assert presentation.verdict(decision, four)["headline"] == "AT RISK — 1 of 3 approval conditions open"
    assert presentation.verdict({"readiness_status": "NOT READY"}, one)["headline"] == "NOT READY — 1 of 1 release actions open"
    done = [{"status": ActionStatus.VERIFIED, "outcome_status": "ACHIEVED"}] * 3
    v = presentation.verdict(decision, done)
    assert v["status"] == "READY FOR APPROVAL" and v["tone"] == "green"
    assert presentation.verdict({"answer_found": False}) is None


def test_recommended_decision_is_one_item_per_blocker():
    sections = presentation.brief_sections(
        {
            "recommendation": "1) PAY-482 2) SEC-217 3) p95",
            "blockers": [
                {"blocker": "UAT defect", "recommendation": "Retest PAY-482", "owner": "Priya", "due_date": "11 Oct 2026"},
                {"blocker": "Token lifetime", "recommendation": "Validate SEC-217", "owner": "Mei", "due_date": "TBD"},
            ],
        }
    )
    items = sections["recommendation_items"]
    assert [i["text"] for i in items] == ["Retest PAY-482", "Validate SEC-217"]
    assert items[0]["owner"] == "Priya" and items[1]["due_date"] == ""


def test_open_questions_drop_model_notes():
    decision = {
        "missing_information": (
            "The issue tracker excerpt was truncated. Who signs off UAT if Priya is unavailable? "
            "Live Gemini call failed (timeout)."
        )
    }
    assert presentation.open_questions(decision) == ["Who signs off UAT if Priya is unavailable?"]
    assert presentation.open_questions({"open_questions": ["The excerpt is cut off", "Is the DR plan approved?"]}) == [
        "Is the DR plan approved?"
    ]
    assert presentation.open_questions({}) == []


def test_snippets_never_end_mid_word_or_show_pipes():
    text = "Owner: Mei · Due: 12 Oct 2026 · Notes: Must be validated before production approval"
    cut = presentation.snippet(text, 40)
    assert cut.endswith("…") and text.startswith(cut[:-1])
    assert text[len(cut) - 1] in " :"  # the cut lands between words, never inside one
    assert presentation.clean_excerpt("| ID | Owner |\n| --- | --- |\n| SEC-217 | Mei |") == "ID · Owner SEC-217 · Mei"


def test_brief_sections_omit_empty_and_do_not_invent_fields():
    sections = presentation.brief_sections(
        {
            "finding": "Certification is blocking release",
            "recommendation": "Close certification first",
            "blockers": [
                {"blocker": "PCI evidence missing", "owner": "Unassigned", "due_date": "TBD", "priority": "High"},
                {"blocker": ""},
            ],
            "missing_information": "",
        }
    )
    assert sections["finding"].startswith("Certification")
    assert sections["expected_outcome"] == ""
    assert len(sections["blockers"]) == 1
    assert sections["blockers"][0]["priority"] == "High"
    assert presentation.nonempty("Unassigned") is False
    assert presentation.nonempty("TBD") is False


def test_public_stack_rows_show_followups_honestly_and_do_not_leak_project():
    rows = presentation.public_stack_rows(
        [
            {
                "technology": "Gmail (drafts only)",
                "status": "DEMO",
                "detail": "REAL unavailable (no token); project project-secret-123",
                "use": "Approve & Draft Follow-up",
            },
            {
                "technology": "Google Calendar",
                "status": "DEMO",
                "detail": "project 123456789012",
                "use": "Approve & Create Calendar Follow-up",
            },
            {
                "technology": "Firestore",
                "status": "ACTIVE",
                "detail": "Connected",
                "use": "Decisions",
            },
        ]
    )
    by_tech = {r["technology"]: r for r in rows}
    assert by_tech["Gmail (drafts only)"]["status"] == "DEMO"
    assert "project-secret-123" not in by_tech["Gmail (drafts only)"]["detail"]
    assert "123456789012" not in by_tech["Google Calendar"]["detail"]
    assert by_tech["Firestore"]["status"] == "ACTIVE"


def test_followups_are_not_connected_in_demo_mode():
    assert presentation.followups_connected() is False


def test_friendly_source_name_keeps_uat_acronym():
    assert presentation.friendly_source_name("uat_meeting_notes.md") == "UAT Meeting Notes"
    assert presentation.friendly_source_name("security_assessment.md") == "Security Assessment"


def test_evidence_cards_strip_markdown_headings():
    cards = presentation.evidence_cards(
        [{"source": "uat_meeting_notes.md", "text": "# UAT notes\n**PAY-482** still open", "page": 1}]
    )
    assert not cards[0]["excerpt"].startswith("#")
    assert "**" not in cards[0]["excerpt"]
    assert "PAY-482" in cards[0]["excerpt"]


def test_hours_label_uses_minutes_below_an_hour():
    assert presentation.hours_label(0.13) == "8 min"
    assert presentation.hours_label(2.33) == "2.3 hrs"


def test_evidence_snippets_do_not_open_with_a_list_number():
    card = presentation.evidence_cards([{"source": "a.md", "text": "2. QA to attach retest evidence."}])[0]
    assert card["excerpt"] == "QA to attach retest evidence."


def test_status_details_never_show_the_connected_account_email():
    detail = presentation._redact_detail("Gmail drafts + Calendar events as demo.owner+test@example.com · calendar 'demo.owner+test@example.com'")
    assert "@" not in detail
    assert "the connected Google account" in detail


def test_brief_layout_helpers_only_use_recorded_fields():
    blockers = [
        {"blocker": "SEC-217 token lifetime inconsistent", "evidence": ["security_assessment.md", "issue_tracker.md"],
         "owner": "Mei Lin", "priority": "High", "recommendation": "Validate SEC-217"},
        {"blocker": "Customer Profile API p95 above the release gate", "evidence": ["performance_test_report.md", "issue_tracker.md"],
         "owner": "Unassigned", "priority": "Medium", "recommendation": "Deploy cache and retest"},
    ]
    assert presentation.issue_ids("SEC-217 and PAY-482, again SEC-217") == ["SEC-217", "PAY-482"]
    assert presentation.initials("Mei Lin") == "ML" and presentation.initials("Unassigned") == ""
    assert presentation.evidence_map(blockers) == [
        {"source": "security_assessment.md", "supports": ["SEC-217"]},
        {"source": "issue_tracker.md", "supports": ["SEC-217", "Customer Profile API p95…"]},
        {"source": "performance_test_report.md", "supports": ["Customer Profile API p95…"]},
    ]
    stats = presentation.brief_stats({"blockers": blockers, "approval_conditions": ["a", "b", "c"]})
    assert stats == {"blockers": 2, "urgent": 1, "owners": 1, "conditions": 3}


def test_approve_measure_impact_layout_reads_only_recorded_state():
    board = [
        {"action_id": "A1", "title": "Validate SEC-217", "status": "APPROVED", "execution_status": "EXECUTED",
         "outcome_status": "ACHIEVED", "actual_outcome": "SEC-217 closed", "expected_outcome": "SEC-217 closed"},
        {"action_id": "A2", "title": "Retest p95", "status": "APPROVED", "execution_status": "EXECUTED",
         "outcome_status": "PENDING", "actual_outcome": "draft text", "expected_outcome": "p95 < 1.5 s"},
        {"action_id": "A3", "title": "Close PAY-482", "status": "PROPOSED"},
        {"action_id": "A4", "title": "Dropped", "status": "REJECTED"},
    ]
    tally = {t["label"]: t["count"] for t in presentation.approval_tally(board)}
    assert tally == {"Pending": 1, "Approved": 2, "Rejected": 1, "Executed": 2}

    rows = presentation.outcome_rows(board)
    assert [r["state"] for r in rows] == ["done", "open", "open"]  # the rejected contract is not listed
    assert rows[1]["actual"] == ""  # unconfirmed text is never shown as an outcome
    decision = {"approval_conditions": ["a", "b", "c"], "readiness_status": "NOT READY"}
    update = presentation.readiness_update(decision, board)
    assert update["status"] != "READY FOR APPROVAL" and "still open" in update["text"]
    assert presentation.readiness_update(decision, board[1:]) is None  # nothing recorded yet

    done = [dict(a, status="APPROVED", execution_status="EXECUTED", outcome_status="ACHIEVED") for a in board[:3]]
    ready = presentation.readiness_update(decision, done)
    assert ready["status"] == "READY FOR APPROVAL" and "stays with a person" in ready["text"]

    tiles = presentation.impact_tiles({"knowledge_sources_analyzed": 6, "estimated_manual_reading_avoided_hours": 1.8,
                                       "coordination_effort_avoided_minutes": 45.0})
    assert [t["value"] for t in tiles] == ["6", presentation.hours_label(1.8), "45 min"]


def test_status_details_stay_readable_after_redaction(set_config):
    set_config(project_id="project-a1b2c3d4-e5f6-7890-abc", document_ai_processor_id="0f1e2d3c4b5a6978")
    detail = presentation._redact_detail(
        "Processor check failed, local parsers in use: PermissionDenied: 403 Permission "
        "'documentai.processors.get' denied on resource "
        "'//documentai.googleapis.com/projects/project-a1b2c3d4-e5f6-7890-abc/locations/us/processors/0f1e2d3c4b5a6978'"
    )
    assert "documentai.processors.get" in detail and detail.startswith("Processor check failed")
    assert "project-a1b2c3d4" not in detail and "0f1e2d3c4b5a6978" not in detail
    assert "projects/this Cloud project/locations/us/processors/the configured processor" in detail
    assert "this Cloud this Cloud" not in detail


def test_partially_achieved_outcomes_leave_conditions_open():
    from workpilot import scenario

    decision = {"approval_conditions": ["SEC-217 closed", "PAY-482 closed"], "blockers": []}
    board = [
        {"action_id": "A1", "title": "SEC-217", "status": "VERIFIED", "outcome_status": "ACHIEVED"},
        {"action_id": "A2", "title": "PAY-482", "status": "VERIFIED", "outcome_status": "PARTIALLY_ACHIEVED"},
    ]
    v = presentation.verdict(decision, board)
    assert v["status"] == "AT RISK" and v["open"] == 1
    assert scenario.readiness_from_actions(board)["status"] == "NOT READY"
    board[1]["outcome_status"] = "ACHIEVED"
    assert presentation.verdict(decision, board)["status"] == "READY FOR APPROVAL"
    assert scenario.readiness_from_actions(board)["status"] == "READY FOR APPROVAL"


def test_session_actions_filter_to_this_decision():
    board = [
        {"action_id": "ACT-1", "decision_id": "DEC-A", "title": "Now"},
        {"action_id": "ACT-2", "decision_id": "DEC-B", "title": "Other"},
    ]
    visible = presentation.session_actions(board, "DEC-A")
    assert [a["action_id"] for a in visible] == ["ACT-1"]
    assert presentation.session_actions(board) == []  # never the whole workspace


def test_due_labels_are_relative_to_today():
    from datetime import date

    today = date(2026, 10, 11)
    assert presentation.due_label("2026-10-11", today) == "Due today"
    assert presentation.due_label("11 Oct 2026", today) == "Due today"
    assert presentation.due_label("12 October 2026", today) == "Due in 1 day"
    assert presentation.due_label("9 Oct 2026", today) == "Overdue by 2 days"
    assert presentation.due_label("TBD", today) == ""
    docs = {"a.md": "# A\n\n**Date:** 7 October 2026\n", "b.md": "**Export date:** 8 October 2026\n"}
    as_of = presentation.evidence_as_of(["a.md", "b.md", "missing.md"], docs)
    assert as_of == date(2026, 10, 8)


def test_provider_badge_never_says_fallback():
    assert presentation.provider_badge(presentation.RETRIEVAL_ACTIVE) == "Vertex AI Search"
    assert presentation.provider_badge(presentation.RETRIEVAL_FALLBACK) == "Local TF-IDF"
