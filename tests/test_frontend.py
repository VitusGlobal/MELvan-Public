from __future__ import annotations

import unittest
from pathlib import Path
from struct import unpack


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "src" / "melvan" / "static" / "index.html").read_text(encoding="utf-8")
JAVASCRIPT = (ROOT / "src" / "melvan" / "static" / "app.js").read_text(encoding="utf-8")
CSS = (ROOT / "src" / "melvan" / "static" / "styles.css").read_text(encoding="utf-8")
PYPROJECT = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
LOGO = ROOT / "src" / "melvan" / "static" / "assets" / "melvan-logo.png"


class FrontendContractTestCase(unittest.TestCase):
    def test_low_confidence_mapping_has_visible_non_color_cue(self) -> None:
        self.assertIn("Low confidence", JAVASCRIPT)
        self.assertIn('data-lucide="triangle-alert"', JAVASCRIPT)
        self.assertIn("is-low-confidence", CSS)
        self.assertIn("border: 1px dashed", CSS)

    def test_identity_gate_and_role_aware_shell_are_present(self) -> None:
        self.assertIn('id="app-version"', HTML)
        self.assertIn('payload.app_version', JAVASCRIPT)
        self.assertIn('name="viewport"', HTML)
        self.assertIn('id="login-screen"', HTML)
        self.assertIn('autocomplete="username"', HTML)
        self.assertIn('autocomplete="current-password"', HTML)
        self.assertIn("Email address", HTML)
        self.assertIn("Create/reset my password", HTML)
        self.assertIn("Midnight tonight", HTML)
        self.assertIn("7 days", HTML)
        self.assertIn("28 days", HTML)
        self.assertIn('id="password-screen"', HTML)
        self.assertIn('id="password-user-guide-acknowledgement"', HTML)
        self.assertIn('id="user-guide-dialog"', HTML)
        self.assertIn('id="open-user-guide"', HTML)
        self.assertIn('id="user-guide-acknowledged"', HTML)
        self.assertIn("MELVan is an assistant that helps teams capture", HTML)
        self.assertIn("Do use MELVan if you:", HTML)
        self.assertIn("Don’t use MELVan if you:", HTML)
        self.assertIn(
            "I acknowledge that I have read the MELvan", HTML
        )
        self.assertIn("payload.requires_user_guide_acknowledgement", JAVASCRIPT)
        self.assertIn("acknowledgement.required = requiresGuide", JAVASCRIPT)
        self.assertIn("user_guide_acknowledged: Boolean(", JAVASCRIPT)
        self.assertIn(".user-guide-dialog", CSS)
        self.assertIn("dialog.showModal()", JAVASCRIPT)
        self.assertNotIn(">Continue<", HTML)
        self.assertIn('aria-live="assertive"', HTML)
        for behavior in ("submitLogin", "submitRecovery", "submitNewPassword", "is-denied"):
            self.assertIn(behavior, JAVASCRIPT)
        self.assertIn("@keyframes head-no", CSS)
        self.assertIn('id="logout"', HTML)
        for view in ("report", "my-statements", "review", "evidence", "logic-model", "users", "melvan-metrics", "about-melvan"):
            self.assertIn(f'data-view="{view}"', HTML)
            self.assertIn(f'id="view-{view}"', HTML)
        self.assertIn('data-role="reporter"', HTML)
        self.assertIn('data-role="reviewer"', HTML)
        self.assertIn('data-role="administrator"', HTML)
        self.assertIn("function allowedViews()", JAVASCRIPT)
        self.assertIn("function canViewAdministratorMetrics()", JAVASCRIPT)
        self.assertIn('new Set(["logic-model", "about-melvan"])', JAVASCRIPT)
        self.assertEqual(5, HTML.count('src="/assets/melvan-logo.png"'))
        self.assertEqual(5, HTML.count("Capture results on the go using AI"))
        self.assertNotIn("Capture results on the go</small>", HTML)
        self.assertNotIn("Program evidence workspace", HTML)
        self.assertNotIn('class="brand-mark"', HTML)
        self.assertIn("--accent: #9f1410;", CSS)
        self.assertIn("--ink: #333333;", CSS)
        self.assertIn("--paper: #f3f5f4;", CSS)
        self.assertIn("--navigation: #ffffff;", CSS)
        self.assertIn("--control: #e4e7e6;", CSS)
        self.assertIn("--control-active: #cbd1cf;", CSS)
        self.assertIn("background: var(--navigation);", CSS)
        self.assertIn(".tab.is-active {\n  background: transparent;", CSS)
        self.assertIn(".login-screen,\n.role-screen {", CSS)
        self.assertIn(".login-panel,\n.role-panel {", CSS)
        self.assertIn("width: 60px;", CSS)
        self.assertIn(".text-button {", CSS)
        self.assertIn("text-underline-offset: 3px;", CSS)
        self.assertTrue(LOGO.is_file())
        logo_bytes = LOGO.read_bytes()
        self.assertEqual(b"\x89PNG\r\n\x1a\n", logo_bytes[:8])
        self.assertEqual((852, 952), unpack(">II", logo_bytes[16:24]))
        self.assertIn('"static/assets/*"', PYPROJECT)
        self.assertIn('id="role-screen"', HTML)
        self.assertIn("What are you here to do?", HTML)
        for label in ("Own MELvan", "Administer", "Report", "Review"):
            self.assertIn(label, JAVASCRIPT)
        self.assertIn('body: { role: button.dataset.assumeRole }', JAVASCRIPT)
        self.assertIn("state.activeRole ? [state.activeRole]", JAVASCRIPT)

    def test_about_melvan_is_shared_read_only_faq_content(self) -> None:
        tab = HTML.split('data-view="about-melvan"', 1)[0].rsplit('<button', 1)[1]
        self.assertNotIn("data-role=", tab)
        for heading in (
            "Who developed MELvan?",
            "What does MELvan stand for?",
            "What is MELvan?",
            "Why was MELvan made?",
            "How is MELvan integrated into everyday work of leaders?",
            "How does MELvan change the way evaluation data gets collected?",
            "What is the methodology behind MELvan?",
            "Who does what in MELvan?",
            "How is MELvan evaluated as a tool?",
            "What kind of values are behind MELvan?",
            "Is MELvan suitable for my program?",
            "How can I get a version of MELvan for my program?",
            "Are there any free open access resources about MELvan?",
        ):
            self.assertIn(f"<h2>{heading}</h2>", HTML)
        self.assertIn("An AI-assisted approach to capturing program impact", HTML)
        self.assertIn("https://doi.org/10.5281/zenodo.18902209", HTML)
        self.assertIn("https://doi.org/10.5281/zenodo.18456041", HTML)
        self.assertIn("mailto:roxana@vitus.ca", HTML)
        self.assertIn(".about-content section", CSS)
        self.assertIn("overflow-x: auto;", CSS)

    def test_reporter_context_and_history_controls_are_present(self) -> None:
        self.assertIn('id="statement-context"', HTML)
        self.assertIn('id="view-my-statements"', HTML)
        self.assertIn('id="history-filters"', HTML)
        self.assertIn('id="history-status"', HTML)
        self.assertIn('id="history-record-count"', HTML)
        self.assertIn('id="history-classification"', HTML)
        self.assertIn('id="history-mapping"', HTML)
        self.assertIn("Add any context about this statement that you think is useful", HTML)
        self.assertNotIn("<option>Activity</option>", HTML)
        self.assertIn('id="edit-context-dialog"', HTML)
        self.assertIn("function renderMyStatements()", JAVASCRIPT)

    def test_reporter_media_capture_contract_is_present(self) -> None:
        for label in ("Text", "Voice", "Photo"):
            self.assertIn(f"<span>{label}</span>", HTML)
        for control in (
            "start-recording",
            "stop-recording",
            "retry-voice",
            "submit-voice",
            "add-voice-context",
            "open-camera",
            "choose-photo",
            "capture-photo",
            "retry-photo",
            "accept-photo",
        ):
            self.assertIn(f'id="{control}"', HTML)
        self.assertIn(
            "Enter a statement explaining why the photo relates to the program.",
            HTML,
        )
        self.assertIn(
            "Record a voice note (up to 60 seconds) and MELvan will transcribe it for you.",
            HTML,
        )
        self.assertIn(
            '<label for="voice-text">Transcription (editable)</label>',
            HTML,
        )
        self.assertNotIn("Use this transcription", HTML)
        self.assertLess(
            HTML.index('id="submit-voice"'),
            HTML.index('id="retry-voice"'),
        )
        self.assertLess(
            HTML.index('id="retry-voice"'),
            HTML.index('id="add-voice-context"'),
        )
        self.assertIn(
            '$("#report-form").requestSubmit($("#submit-statement"));',
            JAVASCRIPT,
        )
        self.assertIn('$("#statement-context").focus();', JAVASCRIPT)
        self.assertIn(
            "Take or choose a photo and MELvan will transcribe any visible text.",
            HTML,
        )
        self.assertIn(
            '<label for="photo-text">Statement text (editable)</label>',
            HTML,
        )
        self.assertIn(
            'id="retry-photo" type="button"><i data-lucide="rotate-ccw" '
            'aria-hidden="true"></i><span>Replace photo</span>',
            HTML,
        )
        self.assertIn("photo-candidate-actions", HTML)
        self.assertLess(
            HTML.index('id="accept-photo"'),
            HTML.index('id="retry-photo"'),
        )
        self.assertIn("const MAX_VOICE_SECONDS = 60;", JAVASCRIPT)
        self.assertIn(
            "Math.min(MAX_VOICE_SECONDS * targetRate",
            JAVASCRIPT,
        )
        self.assertNotIn("Up to 5 minutes", HTML)
        self.assertIn('facingMode: { ideal: "environment" }', JAVASCRIPT)
        self.assertIn("await video.play();", JAVASCRIPT)
        self.assertIn("await waitForCameraFrame(video);", JAVASCRIPT)
        self.assertIn('id="capture-photo" type="button" disabled', HTML)
        self.assertLess(
            HTML.index('id="capture-photo"'),
            HTML.index('id="close-camera"'),
        )
        self.assertIn("<span>Take photo</span>", HTML)
        self.assertNotIn("<span>Capture photo</span>", HTML)
        self.assertIn("Discard this capture and switch methods?", JAVASCRIPT)
        self.assertIn("statementPhotoView", JAVASCRIPT)
        production = (ROOT / "src" / "melvan" / "production.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("camera=(self), microphone=(self)", production)
        self.assertIn('<span class="tag">Category · ${escapeHtml(analysis.outcome_change_types.join(", "))}</span>', JAVASCRIPT)
        self.assertNotIn("compactTaxonomy", JAVASCRIPT)
        self.assertIn('filters.hidden = state.myStatements.length === 0', JAVASCRIPT)
        self.assertIn('Open statements</option>', JAVASCRIPT)
        self.assertIn('Accepted evidence</option>', JAVASCRIPT)
        self.assertIn('displayed with this filter', JAVASCRIPT)
        self.assertIn('total === 1 ? "record" : "records"', JAVASCRIPT)
        self.assertIn("function submitContextEdit", JAVASCRIPT)
        self.assertIn("percent(mapping.confidence)", JAVASCRIPT)
        self.assertIn("mapping-item-rationale", JAVASCRIPT)
        self.assertNotIn("percent(analysis.mapping_confidence)", JAVASCRIPT)
        self.assertIn("No statements match these filters.", JAVASCRIPT)
        self.assertIn("You have not submitted any statements yet.", JAVASCRIPT)

    def test_logic_model_is_read_only_except_for_admin_version_controls(self) -> None:
        self.assertIn("Read-only", HTML)
        self.assertIn('id="logic-version-select"', HTML)
        self.assertIn("Use this version", HTML)
        self.assertIn('id="confirm-logic-version"', HTML)
        self.assertIn("payload.logic_model", JAVASCRIPT)
        self.assertIn("state.logicModelVersions.length > 1", JAVASCRIPT)
        self.assertNotIn("Preconfigured framework", HTML)
        self.assertNotIn("logic-relationships", JAVASCRIPT)

    def test_admin_controls_cover_users_and_logic_model_builder(self) -> None:
        self.assertIn("New user", HTML)
        self.assertNotIn("Temporary password", HTML)
        self.assertNotIn("reset-password", JAVASCRIPT)
        self.assertIn("Temporary lockout", JAVASCRIPT)
        self.assertIn("Suspend", JAVASCRIPT)
        self.assertIn('maxlength="254"', JAVASCRIPT)
        for heading in ("Last login", "Logins", "Statements", "Reviewed"):
            self.assertIn(f"<span>{heading}</span>", JAVASCRIPT)
        self.assertIn('user.last_login_at ? escapeHtml(formatDate(user.last_login_at)) : "Never"', JAVASCRIPT)
        self.assertIn('user.roles.includes("reporter") ? user.statement_count : "—"', JAVASCRIPT)
        self.assertIn('user.roles.includes("reviewer") ? user.reviewed_statement_count : "—"', JAVASCRIPT)
        self.assertIn('name="first_name" aria-label="First name" type="text"', JAVASCRIPT)
        self.assertIn('name="last_name" aria-label="Last name" type="text"', JAVASCRIPT)
        self.assertNotIn(".temporary-password-output", CSS)
        self.assertNotIn("available-id", JAVASCRIPT)
        self.assertIn('id="view-users"', HTML)
        logic_start = HTML.index('id="view-logic-model"')
        users_start = HTML.index('id="view-users"')
        builder = HTML.index('id="logic-model-editor"')
        model = HTML.index('id="logic-model-view"')
        self.assertLess(logic_start, builder)
        self.assertLess(model, builder)
        self.assertLess(builder, users_start)
        self.assertIn("Update the Logic Model", HTML)
        logic_model_section = HTML.split('id="view-logic-model"', 1)[1].split(
            'id="view-users"', 1
        )[0]
        self.assertNotIn('type="file"', logic_model_section)
        for label in (
            "RECAP", "Add an output", "Save changes for later",
            "Commit updates", "Cancel all changes", "Ultimate Goal",
        ):
            self.assertIn(label, HTML + JAVASCRIPT)
        self.assertIn("Are you sure? None of your changes will be implemented or saved", HTML)

    def test_administrator_has_workspace_lifetime_metrics_view(self) -> None:
        self.assertIn('data-view="melvan-metrics"', HTML)
        self.assertIn('data-role="administrator-metrics"', HTML)
        self.assertIn('id="view-melvan-metrics"', HTML)
        self.assertIn('id="administrator-metrics"', HTML)
        self.assertIn("Overall performance for this workspace", HTML)
        for label in (
            "First-review approval",
            "Correct level",
            "Correct direction",
            "Correct mapping",
            "No hallucination/toxic content",
        ):
            self.assertIn(label, JAVASCRIPT)
        self.assertIn("MELvan performance metrics will appear after the first scored review.", JAVASCRIPT)
        self.assertIn("scored review event", JAVASCRIPT)
        self.assertIn("initial Statement review", JAVASCRIPT)
        self.assertIn("Logic Model update review", JAVASCRIPT)
        self.assertIn("current Evidence record", JAVASCRIPT)
        self.assertIn('/api/admin/metrics', JAVASCRIPT)

    def test_logic_model_builder_uses_section_specific_order_and_controls(self) -> None:
        self.assertIn('const levelOrder = ["ultimate_goal", "long_term_outcome", "medium_term_outcome", "short_term_outcome", "output"]', JAVASCRIPT)
        self.assertIn('const levelLabels = { ultimate_goal:', JAVASCRIPT)
        self.assertIn("Category (optional)", JAVASCRIPT)
        self.assertIn("Other/uncategorized", JAVASCRIPT)
        self.assertIn('state.logicModelDraft.elements.unshift({', JAVASCRIPT)
        self.assertIn('data-outcome-level="${level}"', JAVASCRIPT)
        self.assertIn("Up to ${levelCaps[level]}", JAVASCRIPT)
        self.assertNotIn('id="new-outcome-level"', JAVASCRIPT)
        self.assertIn('level === "ultimate_goal" || items.length >= levelCaps[level]', JAVASCRIPT)
        self.assertIn('item.type === "Output" ? `<div class="field"><label class="field-label">Category (optional)</label>', JAVASCRIPT)
        self.assertIn('!state.logicModel?.is_current', JAVASCRIPT)
        self.assertIn('!state.logicEditing && state.logicModelVersions.length > 1', JAVASCRIPT)
        self.assertIn('data-recap-key="${key}" aria-label="${field.label}"', JAVASCRIPT)
        self.assertIn("use numbering if you want to present ${field.label.toLowerCase()} as a list", JAVASCRIPT)
        self.assertNotIn('data-logic-action="add-recap"', JAVASCRIPT)
        self.assertNotIn('data-logic-action="delete-recap"', JAVASCRIPT)

    def test_recap_reference_and_prompts_are_shared_by_logic_model_views(self) -> None:
        self.assertIn(
            'const RECAP_REFERENCE_URL = "https://www.vitushq.com/en/blog/tired-of-vague-advice-on-writing-a-theory-of-change-narrative-try-the-recap-framework/"',
            JAVASCRIPT,
        )
        for label, prompt in (
            ("Risks", "What could go wrong?"),
            ("Evidence", "Knowledge, data, or experience"),
            ("Context", "Sociopolitical, regulatory, organizational, individual"),
            ("Assumptions", "What needs to be true for this to work?"),
            ("People", "Who is influencing and influenced by this?"),
        ):
            self.assertIn(f'label: "{label}", prompt: "{prompt}"', JAVASCRIPT)
        self.assertIn('href="${RECAP_REFERENCE_URL}" target="_blank" rel="noopener noreferrer">RECAP</a>', JAVASCRIPT)
        self.assertIn("Object.entries(RECAP_FIELDS)", JAVASCRIPT)
        self.assertIn(".recap-reference-link {", CSS)
        self.assertIn(".recap-prompt {", CSS)

    def test_reviewer_workspace_is_present(self) -> None:
        self.assertNotIn("New logic model evidence review", HTML)
        for element_id in (
            "end-review",
            "review-report",
            "review-filters",
            "review-classification",
            "review-mapping",
            "review-record-count",
        ):
            self.assertIn(f'id="{element_id}"', HTML)
        review_heading = HTML.index('<h1>Review</h1>')
        review_count = HTML.index('id="review-record-count"')
        review_filters = HTML.index('id="review-filters"')
        self.assertLess(review_heading, review_count)
        self.assertLess(review_count, review_filters)
        for label in (
            "How did MELvan do?",
            "MELvan lifetime score",
            "First-review approval",
            "Correct level",
            "Correct direction",
            "Correct mapping",
            "No hallucination/toxic content",
            "Not relevant",
            "Change classification/mapping",
            "Provide feedback on the language MELVan used",
        ):
            self.assertIn(label, JAVASCRIPT)
        self.assertNotIn("Create a new Output", JAVASCRIPT)
        self.assertNotIn("Create a new Outcome", JAVASCRIPT)
        self.assertIn("Apply changes and accept as Evidence", JAVASCRIPT)
        self.assertIn("Changes applied and Statement accepted as Evidence.", JAVASCRIPT)
        self.assertIn("Accepted unchanged", JAVASCRIPT)
        self.assertIn("<span>Changed</span>", JAVASCRIPT)
        self.assertIn('class="choice-field field-full mapping-choice-field"', JAVASCRIPT)
        self.assertIn("classification || mapping", JAVASCRIPT)
        self.assertIn("showing for this filter", JAVASCRIPT)
        self.assertIn("state.review.length < 2", JAVASCRIPT)

    def test_evidence_reassessment_contract_is_present(self) -> None:
        for element_id in (
            "review-queue-tabs",
            "statement-review-count",
            "evidence-review-count",
            "evidence-review-panel",
        ):
            self.assertIn(f'id="{element_id}"', HTML)
        for label in (
            "Logic Model updates",
            "What changed",
            "Accepted under",
            "Proposed for",
            "Change MELvan's reassessment",
            "Accept",
            "Change",
            "Not relevant",
        ):
            self.assertIn(label, HTML + JAVASCRIPT)
        self.assertIn("data-suggestion-identity", JAVASCRIPT)
        self.assertIn("suggestion_identity", JAVASCRIPT)
        self.assertIn("confidence_delta_percentage_points", JAVASCRIPT)
        self.assertIn("Confirm Not relevant", JAVASCRIPT)
        self.assertIn("Reason (optional)", JAVASCRIPT)
        self.assertIn("Its earlier acceptance and mappings will be preserved.", JAVASCRIPT)
        self.assertIn("function changeLine(change)", JAVASCRIPT)
        self.assertIn('evidenceTab.hidden = state.evidenceReview.length === 0', JAVASCRIPT)
        self.assertIn('state.reviewQueue = "statements"', JAVASCRIPT)
        self.assertIn(".what-changed", CSS)

    def test_exact_empty_state_messages_are_present(self) -> None:
        self.assertIn("There are no statements awaiting review.", JAVASCRIPT)
        self.assertIn("No evidence has been approved yet.", JAVASCRIPT)

    def test_feedback_controls_are_combined_in_one_form(self) -> None:
        for control in (
            'name="classification"',
            'name="result_direction"',
            'name="outcome_category"',
            'name="outcome_change_types"',
            'name="mapping_ids"',
            'name="reject_proposal"',
            'name="comments"',
        ):
            self.assertIn(control, JAVASCRIPT)
        self.assertIn("data-feedback-form", JAVASCRIPT)
        self.assertIn('class="option-input option-radio" type="radio"', JAVASCRIPT)
        self.assertIn('class="option-input option-checkbox" type="checkbox"', JAVASCRIPT)
        self.assertIn('class="direction-toggle"', JAVASCRIPT)
        self.assertIn("syncOutcomeFields", JAVASCRIPT)
        self.assertIn("Choose at least one Outcome change type.", JAVASCRIPT)
        self.assertEqual(3, JAVASCRIPT.count("outcomeDetailChoices("))
        self.assertIn("Select every applicable mapping", JAVASCRIPT)
        self.assertEqual(
            2,
            JAVASCRIPT.count(
                "Add any comments, corrections, or preferred wording for MELvan's language"
            ),
        )
        for issue_type in (
            "inaccurate_summary",
            "unsupported_or_invented_detail",
            "overstated_causal_claim",
            "inappropriate_terminology_or_tone",
            "unclear_or_unnecessarily_technical_language",
            "other_language_issue",
        ):
            self.assertIn(issue_type, JAVASCRIPT)
        self.assertIn(".option-input:focus-visible + .option-mark", CSS)
        self.assertIn(".direction-toggle input:focus-visible + span", CSS)
        self.assertNotIn('<select name="classification"', JAVASCRIPT)
        self.assertNotIn('<select name="mapping_ids"', JAVASCRIPT)

    def test_responsive_guardrails_are_defined(self) -> None:
        self.assertIn("@media (max-width: 760px)", CSS)
        self.assertIn("@media (max-width: 430px)", CSS)

    def test_hosted_login_and_internal_model_status_contract(self) -> None:
        self.assertIn('input[type="password"]', CSS)
        self.assertIn(
            'element.hidden = !["localhost", "127.0.0.1"].includes(window.location.hostname)',
            JAVASCRIPT,
        )
        self.assertIn("Review statements. Submit evidence", JAVASCRIPT)

    def test_no_match_statement_requires_an_approved_mapping(self) -> None:
        self.assertIn(
            "Consider updating the logic model if you think a new output or outcome is necessary.",
            JAVASCRIPT,
        )
        self.assertIn('data-no-suitable-match', JAVASCRIPT)
        self.assertIn("Apply classification change", JAVASCRIPT)
        self.assertIn("The Statement remains awaiting review.", JAVASCRIPT)

    def test_self_improvement_report_is_accessible_and_policy_attributed(self) -> None:
        for label in (
            "Review-session trend",
            "Learning memory",
            "Insufficient sample",
            "omissions corrected",
            "unsupported mappings removed",
            "high-confidence errors",
            "Mappings rated at least 80% that Reviewers corrected or removed",
        ):
            self.assertIn(label, JAVASCRIPT)
        self.assertIn('class="trend-table"', JAVASCRIPT)
        self.assertIn('<table', JAVASCRIPT)

    def test_administrator_and_reviewer_evidence_pdf_export_controls_are_present(self) -> None:
        self.assertIn('id="export-evidence"', HTML)
        self.assertIn('id="reviewer-export-evidence"', HTML)
        self.assertIn('id="export-evidence" hidden', HTML)
        self.assertIn("Export Evidence PDF", HTML)
        self.assertIn(
            '$("#export-evidence").hidden = !canExportEvidence() || !roles().has("administrator") || state.evidenceCount === 0',
            JAVASCRIPT,
        )
        self.assertIn(
            '$("#reviewer-export-evidence").hidden = !canExportEvidence() || !roles().has("reviewer") || state.evidenceCount === 0',
            JAVASCRIPT,
        )
        self.assertIn('api("/api/evidence-export"', JAVASCRIPT)
        self.assertIn('new Blob([bytes], { type: payload.content_type })', JAVASCRIPT)
        self.assertIn("overflow-wrap: anywhere", CSS)
        self.assertIn("minmax(0, 1fr)", CSS)
        self.assertIn("white-space: nowrap", CSS)
        self.assertIn("user?.roles.includes(\"administrator\")", JAVASCRIPT)

    def test_owner_workspace_nickname_controls_are_clear_and_conditional(self) -> None:
        self.assertIn(">Jump to workspace</span>", HTML)
        self.assertIn('aria-label="Jump to workspace"', HTML)
        self.assertIn("state.workspaces.length > 1", JAVASCRIPT)
        self.assertIn("workspace.nickname", JAVASCRIPT)
        self.assertIn("Workspace nickname", JAVASCRIPT)
        self.assertIn('maxlength="40"', JAVASCRIPT)
        self.assertIn("data-workspace-nickname-field", JAVASCRIPT)
        self.assertIn("Workspace suspended", JAVASCRIPT)

    def test_owner_observer_workspace_is_visibly_read_only(self) -> None:
        self.assertIn('id="workspace-read-only-banner"', HTML)
        self.assertIn('id="workspace-read-only-copy"', HTML)
        self.assertIn("workspaceReadOnly: false", JAVASCRIPT)
        self.assertIn("workspace_read_only", JAVASCRIPT)
        self.assertIn("Viewing ${state.workspaceNickname", JAVASCRIPT)
        self.assertIn("- read only", JAVASCRIPT)
        self.assertIn("!state.workspaceReadOnly && (", JAVASCRIPT)
        self.assertIn("state.workspaceReadOnly ? \"\" :", JAVASCRIPT)
        self.assertIn('$("#new-user").hidden = !canAdminister()', JAVASCRIPT)
        self.assertIn(".workspace-read-only-banner {", CSS)

    def test_owner_administrator_interface_is_distinct(self) -> None:
        for identifier in (
            'id="users-eyebrow"',
            'id="users-title"',
            'id="users-section-heading"',
            'id="new-user-label"',
        ):
            self.assertIn(identifier, HTML)
        self.assertIn('"Administrators" : "Users"', JAVASCRIPT)
        self.assertIn('"Owner" : "Administrator"', JAVASCRIPT)
        self.assertIn('"Create a new administrator" : "New user"', JAVASCRIPT)
        self.assertIn('"Workspace nickname" : "Roles"', JAVASCRIPT)
        self.assertIn('data-owner-administrator="${ownerAdministratorForm}"', JAVASCRIPT)
        self.assertIn('? ["administrator"]', JAVASCRIPT)
        self.assertIn('function canExportEvidence()', JAVASCRIPT)
        self.assertIn(
            'data-workspace-nickname-field><label class="field-label">Workspace nickname</label>',
            JAVASCRIPT,
        )
        self.assertIn(
            'name="workspace_nickname" aria-label="Workspace nickname" type="text" maxlength="40"',
            JAVASCRIPT,
        )
        self.assertNotIn(
            'data-workspace-nickname-field ${isAdministrator ? "" : "hidden"}',
            JAVASCRIPT,
        )


if __name__ == "__main__":
    unittest.main()
