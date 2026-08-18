const fs = require("fs");
const path = require("path");
const { chromium, firefox } = require("playwright");

const root = path.resolve(__dirname, "..");
const staticRoot = path.join(root, "src", "melvan", "static");
const artifacts = path.join(root, "artifacts");
fs.mkdirSync(artifacts, { recursive: true });

const mo4 = {
  id: "MO4",
  logic_model_id: "logic-model-demo-v1",
  label: "More use of evidence-informed practices and program learning in community services",
  element_type: "Outcome",
  level: "medium_term_outcome",
  group_name: null,
  source: "fixture",
  status: "published",
  stable_id: "outcome-2",
};

const logicModel = {
  id: "logic-model-demo-v1",
  database_id: "logic-model-demo-v1",
  title: "Community Resilience Learning Program",
  version: "1.0",
  is_current: true,
  source: "synthetic-public-fixture",
  recap: Object.fromEntries(["risks", "evidence", "context", "assumptions", "people"].map((key) => [key, { stable_id: `recap-${key}`, revision: 1, text: "" }])),
  elements: [
    { id: "O1", display_id: "O1", stable_id: "output-1", revision: 1, number: 1, type: "Output", level: "output", group: "Clinical Care", label: "Training support for community partners completed" },
    { id: "SO1", display_id: "SO1", stable_id: "outcome-1", revision: 1, number: 1, type: "Outcome", level: "short_term_outcome", group: "Clinical Care", label: "Improved delivery capacity among community partners" },
    { id: "MO4", display_id: "MO4", stable_id: "outcome-2", revision: 1, number: 2, type: "Outcome", level: "medium_term_outcome", group: null, label: mo4.label },
    { id: "UG3", display_id: "UG3", stable_id: "outcome-3", revision: 1, number: 3, type: "Outcome", level: "ultimate_goal", group: null, label: "Improved wellbeing and resilience among participating communities" },
  ],
  relationships: [
    { from: "O1", to: "SO1", type: "contributes_to" },
    { from: "SO1", to: "MO4", type: "contributes_to" },
  ],
  annotations: [],
};

function makeStatement() {
  return {
    id: "stmt-ui-smoke",
    original_text: "Community partners are using evidence-informed practices in service planning.",
    reporter_id: "reporter-demo",
    submitted_at: "2026-07-17T10:00:00+00:00",
    status: "awaiting_review",
    active_analysis_id: "analysis-ui-1",
    initial_classification: "Outcome",
    approved_classification: null,
    processing_error: null,
    context_updated_at: "2026-07-17T10:00:00+00:00",
    context: { id: "ctx-ui-1", version: 1, text: "", created_by: "reporter-demo", created_at: "2026-07-17T10:00:00+00:00" },
    review_feedback: [],
    analysis: {
      id: "analysis-ui-1",
      statement_id: "stmt-ui-smoke",
      version: 1,
      summary: "Community partners are using evidence-informed practices in service planning.",
      classification: "Outcome",
      classification_confidence: 0.84,
      classification_rationale: "The Statement describes a change in clinical practice.",
      result_direction: "Positive",
      outcome_category: "B",
      outcome_change_types: ["Practice"],
      match_status: "existing_match",
      mapping_confidence: 0.88,
      mapping_rationale: "The Statement describes use of evidence-informed practice in community services.",
      proposed_element_id: null,
      model_version: "melvan-demo-classifier-v1",
      prompt_version: "statement-analysis-v1",
      logic_model_version: "1.0",
      logic_model_id: "logic-model-demo-v1",
      context_revision_id: "ctx-ui-1",
      reviewer_instruction: null,
      created_at: "2026-07-17T10:00:01+00:00",
    },
    mappings: [
      { ...mo4, rank: 1, confidence: 0.88, confidence_tier: "supported", rationale: "The Statement describes use of evidence-informed practice in community services." },
      {
        id: "SO1",
        display_id: "SO1",
        logic_model_id: "logic-model-demo-v1",
        label: "Improved delivery capacity among community partners",
        element_type: "Outcome",
        level: "short_term_outcome",
        source: "fixture",
        status: "published",
        stable_id: "outcome-1",
        rank: 2,
        confidence: 0.42,
        confidence_tier: "low",
        rationale: "This is a plausible but uncertain secondary relationship.",
      },
    ],
    proposed_element: null,
  };
}

function makeEvidenceAssessment() {
  return {
    id: "assessment-ui-1",
    evidence_id: "evidence-ui-1",
    statement_id: "stmt-evidence-ui-1",
    analysis_id: "analysis-evidence-ui-1",
    original_text: "Community partners are using evidence-informed practices in service planning.",
    summary: "Community partners are using evidence-informed practices in service planning.",
    classification: "Outcome",
    accepted_classification: "Outcome",
    suggested_classification: "Outcome",
    accepted_result_direction: "Positive",
    suggested_result_direction: "Positive",
    mapping_rationale: "The edited outcome remains a defensible match.",
    accepted_mappings: [{ ...mo4, confidence: 0.8, rationale: "Previously approved mapping." }],
    suggested_mappings: [{ ...mo4, confidence: 0.55, rationale: "Reassessed after the Logic Model update." }],
    available_mappings: [mo4],
    accepted_logic_model: { id: "logic-model-demo-v1", version: "1.0" },
    target_logic_model: { id: "logic-model-demo-v2", version: "2.0" },
    change_set: [{
      type: "mapping_confidence_changed",
      affected_node_stable_id: "outcome-2",
      previous_confidence: 0.8,
      proposed_confidence: 0.55,
      confidence_delta_percentage_points: 25,
      explanation: `Confidence for "${mo4.label}" changed from 80% to 55%.`,
    }],
    change_summary: [`Confidence for "${mo4.label}" changed from 80% to 55%.`],
    suggestion_version: 1,
    suggestion_identity: "assessment-ui-1:1",
  };
}

function contentType(file) {
  if (file.endsWith(".html")) return "text/html";
  if (file.endsWith(".css")) return "text/css";
  return "application/javascript";
}

async function installRoutes(page) {
  const store = {
    review: [],
    evidence: [],
    evidenceAssessments: [],
    reviewSession: null,
    reviewReport: null,
    quality: { matches: 0, total: 0, percentage: null },
    logicModel: JSON.parse(JSON.stringify(logicModel)),
    draft: null,
  };
  const users = {
    "owner@example.org": { id: "user-admin", first_name: "MELvan", last_name: "Owner", email: "owner@example.org", roles: ["owner", "administrator"], status: "active", workspace_id: "workspace-owner", last_login_at: "2026-07-18T09:00:00+00:00", total_logins: 1, statement_count: 0, reviewed_statement_count: 0 },
    "admin.alpha@example.org": { id: "user-admin-alpha", first_name: "Alex", last_name: "Administrator", email: "admin.alpha@example.org", roles: ["administrator"], status: "active", workspace_id: "workspace-admin-alpha", workspace_nickname: "Northern pilot", last_login_at: "2026-07-18T09:30:00+00:00", total_logins: 3, statement_count: 0, reviewed_statement_count: 0 },
    "rita@example.org": { id: "user-reporter", first_name: "Rita", last_name: "Reporter", email: "rita@example.org", roles: ["reporter"], status: "active", workspace_id: "workspace-admin-alpha", authentication_status: "temporary_lockout", locked_until: "2026-07-20T10:00:00+00:00", last_login_at: "2026-07-18T10:00:00+00:00", total_logins: 4, statement_count: 3, reviewed_statement_count: 0 },
    "reva@example.org": { id: "user-reviewer", first_name: "Reva", last_name: "Reviewer", email: "reva@example.org", roles: ["reviewer"], status: "active", workspace_id: "workspace-admin-alpha", last_login_at: "2026-07-18T11:00:00+00:00", total_logins: 5, statement_count: 0, reviewed_statement_count: 2 },
    "dana@example.org": { id: "user-dual", first_name: "Dana", last_name: "Dual", email: "dana@example.org", roles: ["reporter", "reviewer"], status: "active", workspace_id: "workspace-admin-alpha", last_login_at: "2026-07-18T12:00:00+00:00", total_logins: 6, statement_count: 2, reviewed_statement_count: 1 },
    "new@example.org": { id: "user-new", first_name: "New", last_name: "Reporter", email: "new@example.org", roles: ["reporter"], status: "active", workspace_id: "workspace-admin-alpha", last_login_at: null, total_logins: 0, statement_count: 0, reviewed_statement_count: 0 },
  };
  const workspaces = [
    { id: "workspace-owner", nickname: "MELvan" },
    { id: "workspace-admin-alpha", nickname: "Northern pilot" },
  ];
  let session = null;
  let passwordChangeUser = null;
  await page.route("http://melvan.local/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const currentUser = session?.user;
    const activeRole = session?.activeRole || null;
    if (url.pathname === "/api/session" && request.method() === "POST") {
      const body = request.postDataJSON();
      if (body.email === "new@example.org" && body.password === "Temporary1!") {
        passwordChangeUser = users["new@example.org"];
        return route.fulfill({ contentType: "application/json", body: JSON.stringify({ requires_password_change: true, password_setup_purpose: "initial_setup", requires_user_guide_acknowledgement: true }) });
      }
      const user = body.password === "Password1!" ? users[String(body.email || "").toLowerCase()] : null;
      if (!user) return route.fulfill({ status: 401, contentType: "application/json", body: JSON.stringify({ error: "Unable to complete sign in." }) });
      const selected = user.roles.length === 1 ? user.roles[0] : null;
      session = { user, activeRole: selected, workspaceId: user.workspace_id };
      return route.fulfill({ contentType: "application/json", body: JSON.stringify({ user, active_role: selected, requires_role_selection: !selected }) });
    }
    if (url.pathname === "/api/session/password" && request.method() === "POST") {
      const body = request.postDataJSON();
      if (!body.user_guide_acknowledged) {
        return route.fulfill({ status: 400, contentType: "application/json", body: JSON.stringify({ error: "Acknowledge that you have read the MELVan User Guide to continue." }) });
      }
      session = { user: passwordChangeUser, activeRole: "reporter", workspaceId: passwordChangeUser.workspace_id };
      passwordChangeUser = null;
      return route.fulfill({ contentType: "application/json", body: JSON.stringify({ user: session.user, active_role: session.activeRole, requires_password_change: false, requires_role_selection: false }) });
    }
    if (url.pathname === "/api/auth/recovery" && request.method() === "POST") {
      return route.fulfill({ status: 202, contentType: "application/json", body: JSON.stringify({ message: "If an account exists for this email address, instructions to reset the password will be sent to it" }) });
    }
    if (url.pathname === "/api/session" && request.method() === "GET") {
      return route.fulfill({ status: currentUser ? 200 : 401, contentType: "application/json", body: JSON.stringify(currentUser ? { user: currentUser, active_role: activeRole, requires_role_selection: !activeRole } : { error: "Sign in to continue." }) });
    }
    if (url.pathname === "/api/session/role" && request.method() === "POST") {
      const role = request.postDataJSON().role;
      if (!currentUser?.roles.includes(role)) return route.fulfill({ status: 403, contentType: "application/json", body: JSON.stringify({ error: "That role is not assigned to this user." }) });
      if (session.activeRole) return route.fulfill({ status: 409, contentType: "application/json", body: JSON.stringify({ error: "Sign out and sign in again to change roles." }) });
      session.activeRole = role;
      return route.fulfill({ contentType: "application/json", body: JSON.stringify({ user: currentUser, active_role: role }) });
    }
    if (url.pathname === "/api/session/logout") {
      session = null;
      return route.fulfill({ contentType: "application/json", body: JSON.stringify({ status: "signed_out" }) });
    }
    if (url.pathname === "/api/owner/workspace" && request.method() === "POST") {
      const workspaceId = request.postDataJSON().workspace_id;
      if (activeRole !== "owner" || !workspaces.some((item) => item.id === workspaceId)) {
        return route.fulfill({ status: 403, contentType: "application/json", body: JSON.stringify({ error: "Your role does not permit this action." }) });
      }
      session.workspaceId = workspaceId;
      return route.fulfill({ contentType: "application/json", body: JSON.stringify({ workspace_id: workspaceId, workspaces }) });
    }
    if (url.pathname === "/api/bootstrap") {
      if (!currentUser) return route.fulfill({ status: 401, contentType: "application/json", body: JSON.stringify({ error: "Sign in to continue." }) });
      const observer = activeRole === "owner" && session.workspaceId !== "workspace-owner";
      const reviewer = activeRole === "reviewer" || observer;
      const reporter = activeRole === "reporter";
      const administrator = activeRole === "administrator" || activeRole === "owner";
      const selectedWorkspace = workspaces.find((item) => item.id === session.workspaceId);
      const visibleUsers = activeRole === "owner" && !observer
        ? Object.values(users).filter((item) => item.roles.includes("administrator") && !item.roles.includes("owner"))
        : observer
          ? Object.values(users).filter((item) => item.workspace_id === session.workspaceId)
          : administrator
            ? Object.values(users).filter((item) => item.workspace_id === session.workspaceId)
            : [];
      return route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          app_version: "0.5.1",
          user: currentUser,
          active_role: activeRole,
          review: reviewer ? store.review : [],
          evidence: reviewer ? store.evidence : [],
          evidence_review: reviewer ? store.evidenceAssessments : [],
          quality: reviewer ? store.quality : { matches: 0, total: 0, percentage: null },
          elements: reviewer ? [mo4] : [],
          logic_model: store.logicModel,
          logic_model_draft: administrator ? store.draft : null,
          logic_model_versions: administrator ? [
            ...(store.draft ? [{ id: "draft", version: "In progress", is_current: false, is_draft: true }] : []),
            { id: store.logicModel.database_id, version: store.logicModel.version, is_current: true },
            { id: "logic-model-demo-v0", version: "0.9", is_current: false },
          ] : [],
          users: visibleUsers,
          workspaces: activeRole === "owner" ? workspaces : [],
          workspace_id: session.workspaceId,
          workspace_nickname: selectedWorkspace?.nickname || null,
          workspace_read_only: observer,
          my_statements: reporter ? [...store.review, ...store.evidence].filter((item) => item.reporter_id === currentUser.id) : [],
          counts: { review: reviewer ? store.review.length : 0, evidence: (reviewer || administrator) ? store.evidence.length : 0, evidence_review: reviewer ? store.evidenceAssessments.length : 0 },
          review_session: reviewer ? store.reviewSession : null,
          review_report: reviewer ? store.reviewReport : null,
          model: { mode: "demo", model: "melvan-demo-classifier-v1", status: "ready" },
        }),
      });
    }
    if (url.pathname === "/api/review" && request.method() === "GET") {
      if (store.review.length && !store.reviewSession) {
        store.reviewSession = { id: "review-session-ui", reviewer_id: currentUser.id, status: "open", started_at: "2026-07-18T17:00:00+00:00" };
        store.reviewReport = null;
      }
      return route.fulfill({ contentType: "application/json", body: JSON.stringify({ statements: store.review, session: store.reviewSession, report: store.reviewReport }) });
    }
    if (url.pathname === "/api/evidence-assessments" && request.method() === "GET") {
      return route.fulfill({ contentType: "application/json", body: JSON.stringify({ assessments: store.evidenceAssessments }) });
    }
    if (url.pathname.startsWith("/api/evidence-assessments/") && url.pathname.endsWith("/decision") && request.method() === "POST") {
      const id = url.pathname.split("/")[3];
      store.evidenceAssessments = store.evidenceAssessments.filter((item) => item.id !== id);
      return route.fulfill({ contentType: "application/json", body: JSON.stringify({ assessment: { assessment_id: id, status: request.postDataJSON().action === "not_relevant" ? "not_relevant" : "approved" } }) });
    }
    if (url.pathname === "/api/review/end" && request.method() === "POST") {
      store.reviewSession = null;
      store.reviewReport ||= makeReviewReport({ reviewed: 0, accepted: 0, sent_back: 0, not_relevant: 0 });
      return route.fulfill({ contentType: "application/json", body: JSON.stringify({ report: store.reviewReport }) });
    }
    if (url.pathname === "/api/admin/logic-model-draft/start" && request.method() === "POST") {
      store.draft ||= {
        title: store.logicModel.title,
        title_revision: 1,
        recap: JSON.parse(JSON.stringify(store.logicModel.recap)),
        elements: JSON.parse(JSON.stringify(store.logicModel.elements)),
      };
      return route.fulfill({ contentType: "application/json", body: JSON.stringify({ draft: store.draft }) });
    }
    if (url.pathname === "/api/admin/logic-model-draft/save" && request.method() === "POST") {
      store.draft = request.postDataJSON().draft;
      store.draft.elements.forEach((item, index) => {
        item.number ||= index + 1;
        item.display_id ||= item.type === "Output" ? `O${item.number}` : `SO${item.number}`;
      });
      return route.fulfill({ contentType: "application/json", body: JSON.stringify({ draft: store.draft }) });
    }
    if (url.pathname === "/api/admin/logic-model-draft/commit-summary" && request.method() === "GET") {
      return route.fulfill({ contentType: "application/json", body: JSON.stringify({ summary: { recap_changes: 1, outputs_added: 0, outcomes_added: 0, elements_edited: 0, elements_deleted: 0, impact: { rationale: "Logic Model name and RECAP changes do not affect classification or mapping." } } }) });
    }
    if (url.pathname === "/api/admin/logic-model-draft/commit" && request.method() === "POST") {
      store.logicModel = { ...store.logicModel, ...store.draft, version: "2.0", is_current: true };
      store.draft = null;
      return route.fulfill({ contentType: "application/json", body: JSON.stringify({ logic_model: store.logicModel }) });
    }
    if (url.pathname === "/api/admin/logic-model-draft/cancel" && request.method() === "POST") {
      store.draft = null;
      return route.fulfill({ contentType: "application/json", body: JSON.stringify({ status: "cancelled" }) });
    }
    if (url.pathname === "/api/statements" && request.method() === "POST") {
      const statement = makeStatement();
      const body = request.postDataJSON();
      store.lastStatementBody = body;
      statement.original_text = body.text;
      statement.reporter_id = currentUser.id;
      statement.context.text = body.context || "";
      store.review = [statement];
      return route.fulfill({ status: 201, contentType: "application/json", body: JSON.stringify({ statement }) });
    }
    if (url.pathname.endsWith("/context") && request.method() === "POST") {
      const statement = [...store.review, ...store.evidence].find((item) => url.pathname.includes(item.id));
      statement.context = { ...statement.context, id: "ctx-ui-2", version: 2, text: request.postDataJSON().context, created_at: "2026-07-17T10:03:00+00:00" };
      statement.analysis = { ...statement.analysis, id: "analysis-ui-context-2", version: 2, context_revision_id: "ctx-ui-2" };
      statement.active_analysis_id = statement.analysis.id;
      return route.fulfill({ contentType: "application/json", body: JSON.stringify({ statement }) });
    }
    if (url.pathname.endsWith("/feedback") && request.method() === "POST") {
      const statement = store.review.shift();
      statement.analysis = {
        ...statement.analysis,
        id: "analysis-ui-2",
        version: 2,
        classification_confidence: 1,
        classification_rationale: "Applied directly from authoritative Reviewer feedback.",
      };
      statement.active_analysis_id = statement.analysis.id;
      statement.status = "evidence";
      statement.approved_classification = statement.analysis.classification;
      statement.evidence = {
        id: "evidence-ui-smoke",
        statement_id: statement.id,
        analysis_id: statement.analysis.id,
        accepted_at: "2026-07-17T10:05:00+00:00",
      };
      store.evidence = [statement];
      store.quality = { matches: 1, total: 1, percentage: 100 };
      store.reviewSession = null;
      store.reviewReport = makeReviewReport({ reviewed: 1, accepted: 0, sent_back: 1, not_relevant: 0 });
      return route.fulfill({ contentType: "application/json", body: JSON.stringify({ statement }) });
    }
    if (url.pathname.endsWith("/accept") && request.method() === "POST") {
      const statement = store.review.shift();
      statement.status = "evidence";
      statement.approved_classification = statement.analysis.classification;
      statement.evidence = {
        id: "evidence-ui-smoke",
        statement_id: statement.id,
        analysis_id: statement.analysis.id,
        accepted_at: "2026-07-17T10:05:00+00:00",
      };
      store.evidence = [statement];
      store.quality = { matches: 1, total: 1, percentage: 100 };
      store.reviewSession = null;
      store.reviewReport = makeReviewReport({ reviewed: 1, accepted: 1, sent_back: 1, not_relevant: 0 });
      return route.fulfill({ contentType: "application/json", body: JSON.stringify({ statement }) });
    }
    if (url.pathname.endsWith("/not-relevant") && request.method() === "POST") {
      store.review = store.review.filter((statement) => !url.pathname.includes(statement.id));
      store.reviewSession = null;
      store.reviewReport = makeReviewReport({ reviewed: 1, accepted: 0, sent_back: 0, not_relevant: 1 });
      return route.fulfill({ contentType: "application/json", body: JSON.stringify({ statement: { status: "not_relevant" } }) });
    }

    const files = {
      "/": "index.html",
      "/index.html": "index.html",
      "/styles.css": "styles.css",
      "/app.js": "app.js",
      "/vendor/lucide.min.js": path.join("vendor", "lucide.min.js"),
    };
    const relative = files[url.pathname];
    if (!relative) return route.fulfill({ status: 404, body: "Not found" });
    const file = path.join(staticRoot, relative);
    return route.fulfill({ contentType: contentType(file), body: fs.readFileSync(file) });
  });
  return store;
}

function makeReviewReport(counts) {
  const score = (matches, total) => ({ matches, total, percentage: total ? matches / total * 100 : null });
  const sessionScores = {
    first_review_approval: score(0, counts.sent_back ? 1 : counts.accepted ? 1 : 0),
    correct_level: score(counts.accepted || counts.sent_back ? 1 : 0, counts.accepted || counts.sent_back ? 1 : 0),
    correct_direction: score(counts.accepted || counts.sent_back ? 1 : 0, counts.accepted || counts.sent_back ? 1 : 0),
    correct_mapping: score(counts.sent_back ? 0 : counts.accepted ? 1 : 0, counts.accepted || counts.sent_back ? 1 : 0),
    overall_hygiene: score(counts.accepted || counts.sent_back ? 1 : 0, counts.accepted || counts.sent_back ? 1 : 0),
  };
  return { session_id: "review-session-ui", reviewer_id: "user-reviewer", completion_reason: "exhausted", counts, session_scores: sessionScores, lifetime_scores: sessionScores };
}

async function assertNoOverflow(page, label) {
  const dimensions = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    clientWidth: document.documentElement.clientWidth,
  }));
  if (dimensions.scrollWidth > dimensions.clientWidth) {
    throw new Error(`${label} has horizontal overflow: ${JSON.stringify(dimensions)}`);
  }
}

async function signIn(page, email, password = "Password1!") {
  await page.getByLabel("Email address").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
}

(async () => {
  let browser;
  try {
    browser = await chromium.launch({
      headless: true,
      executablePath: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    });
  } catch (chromeError) {
    try {
      browser = await firefox.launch({
        headless: true,
        executablePath: "/Applications/Firefox.app/Contents/MacOS/firefox",
      });
    } catch (firefoxError) {
      throw new Error(
        `No browser could launch. Chrome: ${chromeError.message}; Firefox: ${firefoxError.message}`
      );
    }
  }
  const errors = [];
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    page.on("console", (message) => {
      if (message.type() === "error") errors.push(message.text());
    });
    page.on("pageerror", (error) => errors.push(error.message));
    await installRoutes(page);
    await page.goto("http://melvan.local/", { waitUntil: "networkidle" });

    await page.getByText("Sign in to MELvan").waitFor();
    await signIn(page, "unknown@example.org", "WrongPassword1!");
    await page.locator("#login-status").waitFor({ state: "visible" });
    const denial = await page.evaluate(() => ({
      active: document.activeElement?.id,
      statusBox: document.querySelector("#login-status").getBoundingClientRect().toJSON(),
    }));
    if (denial.active !== "login-password" || denial.statusBox.width > 1 || denial.statusBox.height > 1) {
      throw new Error(`Denied password login did not retain the generic response: ${JSON.stringify(denial)}`);
    }
    await page.getByRole("button", { name: "Create/reset my password" }).click();
    await page.getByRole("heading", { name: "Create or reset your password" }).waitFor();
    await page.getByLabel("Email address").fill("new@example.org");
    await page.getByRole("button", { name: "Send instructions" }).click();
    await page.getByText("If an account exists for this email address").waitFor();
    await page.getByRole("button", { name: "Return to sign in" }).click();
    await signIn(page, "new@example.org", "Temporary1!");
    await page.getByRole("heading", { name: "Create your password" }).waitFor();
    await page.getByRole("button", { name: "user guide", exact: true }).click();
    await page.getByRole("heading", { name: "MELvan User Guide" }).waitFor();
    await page.getByText("Do use MELVan if you:").waitFor();
    await page.getByText("Don’t use MELVan if you:").waitFor();
    await page.getByRole("button", { name: "Close user guide" }).click();
    if (await page.locator("#user-guide-dialog").isVisible()) throw new Error("User Guide dialog did not close.");
    await page.getByLabel("New password").fill("NewStrong1!");
    await page.getByLabel("Confirm password").fill("NewStrong1!");
    await page.getByRole("button", { name: "Create password" }).click();
    if (!(await page.locator("#password-screen").isVisible())) throw new Error("Initial password was accepted without User Guide acknowledgement.");
    await page.getByLabel("I acknowledge that I have read the MELvan user guide").check();
    await page.getByRole("button", { name: "Create password" }).click();
    await page.getByText("New Reporter").waitFor();
    if (await page.locator("#password-screen").isVisible()) throw new Error("Create password remains visible after successful replacement.");
    await page.getByRole("button", { name: "Sign out" }).click();
    await page.getByLabel("Email address").fill("");
    await signIn(page, "dana@example.org");
    await page.getByText("What are you here to do?").waitFor();
    await page.getByRole("button", { name: /Report Capture/ }).click();
    await page.getByText("Dana Dual").waitFor();
    if (await page.getByRole("tab", { name: "Users" }).isVisible()) throw new Error("Reporter session can see Users controls.");
    if (await page.getByRole("tab", { name: "Review", exact: true }).isVisible()) throw new Error("Reporter session can see Review controls.");
    const contextPrompt = "Add any context about this statement that you think is useful";
    if (await page.getByPlaceholder(contextPrompt).count() !== 1) throw new Error("The neutral Context prompt is missing.");
    await page.getByRole("tab", { name: "Voice" }).click();
    await page.getByText("Record a voice note (up to 60 seconds) and MELvan will transcribe it for you.", { exact: true }).waitFor();
    await page.getByText("Your recording is discarded after transcription.", { exact: true }).waitFor();
    await page.getByText("Up to 60 seconds", { exact: true }).waitFor();
    await page.getByRole("button", { name: "Record statement" }).waitFor();
    await assertNoOverflow(page, "desktop Voice capture");
    await page.screenshot({ path: path.join(artifacts, "voice-capture-desktop.png"), fullPage: true });
    await page.getByRole("tab", { name: "Text" }).click();
    await page.getByRole("tab", { name: "My statements" }).click();
    if (await page.locator("#history-filters").isVisible()) throw new Error("Filters are visible without Reporter records.");
    await page.getByText("0 records", { exact: true }).waitFor();
    await page.getByRole("tab", { name: "Report", exact: true }).click();
    await page.getByLabel("Statement").fill(
      "Community partners are using evidence-informed practices in service planning."
    );
    await page.getByLabel("Context", { exact: true }).fill("Observed during the regional implementation meeting.");
    await page.getByRole("button", { name: "Submit statement" }).click();
    await page.getByText("Statement captured and ready for review.").waitFor();
    await page.getByRole("tab", { name: "My statements" }).click();
    await page.locator("#my-statements-list").getByText("Observed during the regional implementation meeting.").waitFor();
    await page.getByText("1 record", { exact: true }).waitFor();
    await page.locator("#my-statements-list").getByText("Outcome", { exact: true }).waitFor();
    await page.locator("#my-statements-list").getByText("Positive", { exact: true }).waitFor();
    await page.locator("#my-statements-list").getByText("Category · Practice", { exact: true }).waitFor();
    if (await page.locator("#my-statements-list").getByText("Positive outcome", { exact: true }).count()) {
      throw new Error("My statements repeats the classification in its direction badge.");
    }
    if (await page.locator("#my-statements-list").getByText("Category B · Practice", { exact: true }).count()) {
      throw new Error("My statements exposes the internal Outcome category letter.");
    }
    await page.locator("#my-statements-list").getByText("88% confidence", { exact: true }).waitFor();
    await page.locator("#my-statements-list").getByText("The Statement describes use of evidence-informed practice in community services.", { exact: true }).waitFor();
    if (await page.locator("#my-statements-list").getByText("Logic Model mapping 88% confidence", { exact: true }).count()) {
      throw new Error("Aggregate mapping confidence is still displayed.");
    }
    await page.getByLabel("Classification").selectOption("Outcome");
    await page.getByLabel("Logic Model element").selectOption("outcome-2");
    await page.getByText("1 of 1 record displayed with this filter", { exact: true }).waitFor();
    await page.getByRole("button", { name: "Edit Context" }).click();
    await page.locator("#edit-context-text").fill("Updated regional implementation context.");
    await page.getByRole("button", { name: "Save Context" }).click();
    await page.locator("#my-statements-list").getByText("Updated regional implementation context.").waitFor();
    await assertNoOverflow(page, "desktop My statements");
    await page.screenshot({ path: path.join(artifacts, "my-statements-desktop.png"), fullPage: true });

    await page.getByRole("button", { name: "Sign out" }).click();
    await signIn(page, "reva@example.org");
    await page.getByText("Reva Reviewer").waitFor();
    if (await page.getByRole("tab", { name: "Report", exact: true }).isVisible()) throw new Error("Reviewer session can see Report controls.");

    await page.getByRole("tab", { name: "Review", exact: true }).click();
    await page.locator("#review-list").getByText(mo4.label).first().waitFor();
    if (await page.getByRole("tab", { name: /Logic Model updates/ }).isVisible()) throw new Error("Logic Model updates is visible without pending Evidence reassessment.");
    await page.getByText("1 statement", { exact: true }).waitFor();
    if (await page.locator("#review-filters").isVisible()) throw new Error("Review filters are visible with only one eligible Statement.");
    await page.locator("#review-list").getByText("Positive", { exact: true }).waitFor();
    await page.locator("#review-list").getByText("Category · Practice", { exact: true }).waitFor();
    if (await page.locator("#review-list").getByText("Positive outcome", { exact: true }).count()) {
      throw new Error("Review repeats the classification in its direction badge.");
    }
    if (await page.locator("#review-list").getByText("Category B · Practice", { exact: true }).count()) {
      throw new Error("Review exposes the internal Outcome category letter.");
    }
    await page.locator("#review-list").getByText("Low confidence", { exact: true }).waitFor();
    const lowMapping = page.locator("#review-list .mapping-item.is-low-confidence");
    if (await lowMapping.count() !== 1) throw new Error("Low-confidence mapping does not have a distinct card treatment.");
    if ((await lowMapping.evaluate((element) => getComputedStyle(element).borderTopStyle)) !== "dashed") {
      throw new Error("Low-confidence mapping is missing its non-color dashed-border cue.");
    }
    await page.getByRole("button", { name: "Change classification/mapping" }).click();
    const mappingField = page.getByRole("group", { name: "Existing mappings" });
    const mappingWidth = await mappingField.evaluate((element) => ({ field: element.getBoundingClientRect().width, panel: element.closest(".feedback-grid").getBoundingClientRect().width }));
    if (mappingWidth.field < mappingWidth.panel * 0.9) throw new Error(`Existing mappings is not full width: ${JSON.stringify(mappingWidth)}`);
    if (!(await page.getByRole("radio", { name: "Outcome" }).isChecked())) throw new Error("Current classification is not preselected.");
    if (!(await page.getByRole("radio", { name: "Positive" }).isChecked())) throw new Error("Current Outcome direction is not preselected.");
    if (!(await page.getByRole("checkbox", { name: new RegExp(mo4.label) }).isChecked())) throw new Error("Current mapping is not preselected.");
    const tentativeChoice = page.getByRole("checkbox", { name: /Improved precision clinical decision-making.*Low confidence \(42%\)/ });
    if (!(await tentativeChoice.isChecked())) throw new Error("Low-confidence mapping is not independently preselected.");
    await page.getByRole("radio", { name: "Output" }).check();
    if (await page.getByRole("group", { name: "Outcome direction" }).isVisible()) throw new Error("Outcome direction remains visible for Output.");
    await page.getByRole("radio", { name: "Outcome" }).check();
    if (await page.getByText("Create a new Output", { exact: true }).count()) throw new Error("Reviewer can create a new Output.");
    if (await page.getByPlaceholder("Add any comments, corrections, or preferred wording for MELvan's language").count() !== 1) throw new Error("Statement Comments guidance is missing.");
    await page.getByRole("group", { name: /Provide feedback on the language MELVan used/ }).getByText("Unsupported or invented detail", { exact: true }).waitFor();
    await page.getByLabel("Comments").fill("The classification and mapping are correct.");
    await page.getByRole("button", { name: "Apply changes and accept as Evidence" }).click();
    await page.getByText("Changes applied and Statement accepted as Evidence.").waitFor();
    await page.getByText("There are no statements awaiting review.").waitFor();
    await page.getByRole("heading", { name: "How did MELvan do?" }).waitFor();
    await page.getByText("MELvan lifetime score").waitFor();
    for (const label of ["First-review approval", "Correct level", "Correct direction", "Correct mapping", "No hallucination/toxic content"]) {
      await page.getByText(label, { exact: true }).first().waitFor();
    }
    await page.getByText("Mappings rated at least 80% that Reviewers corrected or removed", { exact: true }).waitFor();
    await page.getByText("low-confidence mappings", { exact: true }).waitFor();
    await assertNoOverflow(page, "desktop Review completion report");
    await page.screenshot({ path: path.join(artifacts, "review-report-desktop.png"), fullPage: true });
    await page.getByRole("tab", { name: "Evidence", exact: true }).click();
    await page.locator("#evidence-list").getByText(mo4.label).first().waitFor();
    await page.getByRole("button", { name: "Export Evidence PDF" }).waitFor();
    await page.getByText("100%").waitFor();
    await assertNoOverflow(page, "desktop Evidence");
    await page.screenshot({ path: path.join(artifacts, "evidence-desktop.png"), fullPage: true });

    await page.getByRole("button", { name: "Sign out" }).click();
    await signIn(page, "dana@example.org");
    await page.getByRole("button", { name: /Report Capture/ }).click();
    await page.getByRole("tab", { name: "My statements" }).click();
    await page.locator("#my-statements-list").getByText("Evidence", { exact: true }).waitFor();
    await page.getByLabel("Record type").selectOption("evidence");
    if (await page.locator("#my-statements-list .record-card").count() !== 1) throw new Error("Accepted Evidence filter did not retain the Evidence record.");
    if (await page.getByRole("button", { name: "Edit Context" }).count()) throw new Error("Evidence exposes Context editing.");

    const narrow = await browser.newPage({ viewport: { width: 360, height: 800 } });
    narrow.on("console", (message) => {
      if (message.type() === "error") errors.push(message.text());
    });
    narrow.on("pageerror", (error) => errors.push(error.message));
    const narrowStore = await installRoutes(narrow);
    narrowStore.review = [makeStatement()];
    narrowStore.evidenceAssessments = [makeEvidenceAssessment()];
    await narrow.goto("http://melvan.local/#review", { waitUntil: "networkidle" });
    await signIn(narrow, "reva@example.org");
    await narrow.locator("#review-list").getByText(mo4.label).first().waitFor();
    if (await narrow.getByRole("tab", { name: "Report" }).isVisible()) throw new Error("Reviewer can see Report controls.");
    if (!(await narrow.getByRole("tab", { name: /Logic Model updates/ }).isVisible())) throw new Error("Logic Model updates is hidden despite pending Evidence reassessment.");
    await assertNoOverflow(narrow, "narrow Review");
    await narrow.getByRole("tab", { name: /Logic Model updates/ }).click();
    await narrow.getByRole("heading", { name: "What changed" }).waitFor();
    await narrow.getByText("25 percentage points").waitFor();
    await assertNoOverflow(narrow, "narrow Logic Model Update Evidence Review");
    await narrow.screenshot({ path: path.join(artifacts, "evidence-reassessment-narrow.png"), fullPage: true });
    await narrow.locator("#evidence-review-panel").getByRole("button", { name: "Change" }).click();
    await narrow.getByRole("heading", { name: "Change MELvan's reassessment" }).waitFor();
    const evidenceForm = narrow.locator(".evidence-assessment-form");
    if (!(await evidenceForm.getByRole("radio", { name: "Outcome" }).isChecked())) throw new Error("Proposed Evidence classification is not preselected.");
    if (!(await evidenceForm.getByRole("radio", { name: "Positive" }).isChecked())) throw new Error("Proposed Evidence direction is not preselected.");
    if (!(await evidenceForm.getByRole("checkbox", { name: new RegExp(mo4.label) }).isChecked())) throw new Error("Proposed Evidence mapping is not preselected.");
    await evidenceForm.getByRole("radio", { name: "Negative" }).check();
    if (!(await evidenceForm.getByRole("radio", { name: "Negative" }).isChecked())) throw new Error("Evidence direction control did not accept a correction.");
    if (await evidenceForm.getByPlaceholder("Add any comments, corrections, or preferred wording for MELvan's language").count() !== 1) throw new Error("Evidence Comments guidance is missing.");
    await assertNoOverflow(narrow, "narrow Evidence reassessment change");
    await narrow.getByRole("button", { name: "Close changes" }).click();
    await narrow.locator("#evidence-review-panel").getByRole("button", { name: "Not relevant" }).click();
    await narrow.getByRole("heading", { name: "Confirm Not relevant" }).waitFor();
    await narrow.getByText("Reason (optional)").waitFor();
    await assertNoOverflow(narrow, "narrow Evidence Not relevant confirmation");
    await narrow.getByRole("button", { name: "Cancel Not relevant" }).click();
    await narrow.getByRole("tab", { name: /Statements/ }).click();
    await narrow.getByRole("button", { name: "Change" }).click();
    await assertNoOverflow(narrow, "narrow feedback");
    await narrow.screenshot({ path: path.join(artifacts, "review-narrow.png"), fullPage: true });
    narrow.once("dialog", async (dialog) => {
      if (!dialog.message().includes("not relevant to the program")) throw new Error(`Unexpected confirmation: ${dialog.message()}`);
      await dialog.accept();
    });
    await narrow.getByRole("button", { name: "Not relevant" }).click();
    await narrow.getByRole("heading", { name: "How did MELvan do?" }).waitFor();
    await narrow.getByText("1", { exact: true }).last().waitFor();
    await assertNoOverflow(narrow, "narrow Review completion report");
    await narrow.getByRole("tab", { name: "Logic model" }).click();
    await narrow.getByText(logicModel.title).waitFor();
    await assertNoOverflow(narrow, "narrow Logic model");
    await narrow.screenshot({ path: path.join(artifacts, "logic-model-narrow.png"), fullPage: true });
    await narrow.getByRole("button", { name: "Sign out" }).click();
    await signIn(narrow, "dana@example.org");
    await narrow.getByRole("button", { name: /Report Capture/ }).click();
    await narrow.getByRole("tab", { name: "Voice" }).click();
    await narrow.getByText("Record a voice note (up to 60 seconds) and MELvan will transcribe it for you.", { exact: true }).waitFor();
    await narrow.getByText("Up to 60 seconds", { exact: true }).waitFor();
    await narrow.evaluate(() => {
      state.capture.voice = {
        status: "candidate_ready",
        original: "The voice transcription can be edited before submission.",
        accepted: false,
        meta: {
          provider: "ui-smoke",
          provider_version: "1",
          processing_ms: 12,
          outcome: "transcribed",
        },
      };
      document.querySelector("#voice-text").value = state.capture.voice.original;
      document.querySelector("#voice-candidate").hidden = false;
      updateCaptureReadyState();
    });
    await narrow.getByText("Transcription (editable)", { exact: true }).waitFor();
    const voiceActions = narrow.locator("#voice-candidate .candidate-actions");
    const voiceButtons = await voiceActions.getByRole("button").allTextContents();
    if (voiceButtons.map((label) => label.trim()).join("|") !== "Submit statement|Try again|Add context") {
      throw new Error(`Voice action order is incorrect: ${JSON.stringify(voiceButtons)}`);
    }
    await voiceActions.getByRole("button", { name: "Add context" }).click();
    if (await narrow.evaluate(() => document.activeElement?.id) !== "statement-context") {
      throw new Error("Add context did not focus the Context field.");
    }
    await narrow.getByLabel("Context", { exact: true }).fill("Context added after reviewing the voice transcription.");
    await narrow.locator("#report-form > .form-footer").getByRole("button", { name: "Submit statement" }).click();
    await narrow.getByText("Statement captured and ready for review.").waitFor();
    if (narrowStore.lastStatementBody?.capture?.method !== "voice") throw new Error("Context-assisted Voice submission lost its capture method.");
    if (narrowStore.lastStatementBody?.context !== "Context added after reviewing the voice transcription.") throw new Error("Context-assisted Voice submission lost its Context.");

    await narrow.getByRole("tab", { name: "Voice" }).click();
    await narrow.evaluate(() => {
      state.capture.voice = {
        status: "candidate_ready",
        original: "This transcription should submit in one click.",
        accepted: false,
        meta: {
          provider: "ui-smoke",
          provider_version: "1",
          processing_ms: 9,
          outcome: "transcribed",
        },
      };
      document.querySelector("#voice-text").value = state.capture.voice.original;
      document.querySelector("#voice-candidate").hidden = false;
      updateCaptureReadyState();
    });
    await narrow.locator("#submit-voice").click();
    await narrow.locator("#voice-candidate").waitFor({ state: "hidden" });
    if (narrowStore.lastStatementBody?.text !== "This transcription should submit in one click.") throw new Error("Direct Voice submission did not use the editable transcription.");
    if (narrowStore.lastStatementBody?.capture?.method !== "voice") throw new Error("Direct Voice submission lost its capture method.");
    await assertNoOverflow(narrow, "narrow Voice capture");
    await narrow.screenshot({ path: path.join(artifacts, "voice-capture-narrow.png"), fullPage: true });

    const admin = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    admin.on("console", (message) => { if (message.type() === "error") errors.push(message.text()); });
    admin.on("pageerror", (error) => errors.push(error.message));
    const adminStore = await installRoutes(admin);
    await admin.goto("http://melvan.local/", { waitUntil: "networkidle" });
    await signIn(admin, "owner@example.org");
    await admin.getByRole("button", { name: /Own MELvan/ }).click();
    await admin.getByRole("tab", { name: "Users" }).waitFor();
    if (await admin.getByRole("button", { name: "Export Evidence PDF" }).isVisible()) throw new Error("Evidence export is visible with no Evidence.");
    if (await admin.getByRole("tab", { name: "Report" }).isVisible()) throw new Error("Administrator can see Report without that role.");
    if (await admin.getByRole("tab", { name: "Review" }).isVisible()) throw new Error("Administrator can see Review without that role.");
    await admin.getByRole("tab", { name: "Logic model" }).click();
    await admin.getByLabel("Version").waitFor();
    if (!await admin.locator("#logic-version-toolbar").isVisible()) throw new Error("Version selector is hidden with multiple versions.");
    await admin.getByText("Update the Logic Model").waitFor();
    const recapReference = admin.locator("#logic-model-view").getByRole("link", { name: "RECAP", exact: true });
    await recapReference.waitFor();
    if (await recapReference.getAttribute("href") !== "https://example.org/recap") throw new Error("RECAP reference URL is incorrect.");
    if (await recapReference.getAttribute("target") !== "_blank" || await recapReference.getAttribute("rel") !== "noopener noreferrer") throw new Error("RECAP reference does not open safely in a new tab.");
    for (const prompt of [
      "What could go wrong?",
      "Knowledge, data, or experience",
      "Sociopolitical, regulatory, organizational, individual",
      "What needs to be true for this to work?",
      "Who is influencing and influenced by this?",
    ]) {
      await admin.locator("#logic-model-view").getByText(prompt, { exact: true }).waitFor();
    }
    const logicModelView = await admin.locator("#logic-model-view").evaluate((element) => element.getBoundingClientRect().top);
    const logicUpdater = await admin.locator("#logic-model-admin-action").evaluate((element) => element.getBoundingClientRect().top);
    if (logicModelView >= logicUpdater) throw new Error("Logic model updater appears before the current logic model.");
    await admin.getByRole("button", { name: "Update the Logic Model" }).click();
    await admin.locator("#logic-model-editor").getByRole("link", { name: "RECAP", exact: true }).waitFor();
    if (await admin.locator("#logic-version-toolbar").isVisible()) throw new Error("Version selector remains visible while editing.");
    for (const heading of ["Risks", "Evidence", "Context", "Assumptions", "People", "Outputs", "Outcomes", "Ultimate Goal"]) {
      if (!await admin.getByRole("heading", { name: heading, exact: true }).count()) throw new Error(`${heading} is missing from the Logic Model builder.`);
    }
    await admin.getByLabel("Context", { exact: true }).fill("Regional implementation context");
    for (const prompt of [
      "What could go wrong?",
      "Knowledge, data, or experience",
      "Sociopolitical, regulatory, organizational, individual",
      "What needs to be true for this to work?",
      "Who is influencing and influenced by this?",
    ]) {
      await admin.locator("#logic-model-editor").getByText(prompt, { exact: true }).waitFor();
    }
    await admin.getByText("use numbering if you want to present risks as a list e.g. 1. This is risk one. 2. This is risk two.").waitFor();
    await admin.getByLabel("Logic Model name").fill("Updated Community Logic Model");
    await admin.getByText("Draft saved").waitFor();
    await admin.getByRole("button", { name: "Commit updates" }).click();
    await admin.getByRole("heading", { name: "Commit Logic Model updates?" }).waitFor();
    await admin.getByRole("button", { name: "Commit updates" }).click();
    await admin.getByText("Updated Community Logic Model").waitFor();
    adminStore.evidence = [{}];
    await admin.getByRole("tab", { name: "Users" }).click();
    await admin.getByRole("heading", { name: "Administrators", exact: true }).waitFor();
    await admin.getByText("Owner", { exact: true }).waitFor();
    if (await admin.getByText("User access", { exact: true }).count()) throw new Error("Owner page still says User access.");
    if (await admin.getByText("Reporter and Reviewer access", { exact: true }).count()) throw new Error("Owner page still describes Reporter and Reviewer access.");
    if (await admin.getByRole("button", { name: "Export Evidence PDF" }).isVisible()) throw new Error("Owner can export Evidence.");
    await admin.getByText("Workspace nickname", { exact: true }).waitFor();
    await admin.getByText("Northern pilot", { exact: true }).waitFor();
    if (await admin.getByText("Rita Reporter", { exact: true }).count()) throw new Error("Owner home lists an ordinary workspace user.");
    if (await admin.locator("#logic-model-admin-action").isVisible()) throw new Error("Logic Model controls appear on Users.");
    if (await admin.getByRole("button", { name: /Reset password for/ }).count()) throw new Error("Privileged password reset remains visible.");
    const statementsHeading = admin.locator(".users-header > span").filter({ hasText: /^Statements$/ });
    const statementHeadingBox = await statementsHeading.evaluate((element) => element.getBoundingClientRect());
    if (statementHeadingBox.height > 20) throw new Error("Statements heading wraps onto multiple lines.");
    await admin.getByRole("button", { name: "Create a new administrator" }).click();
    await admin.getByRole("heading", { name: "Create a new administrator", exact: true }).waitFor();
    const email = admin.locator('.user-form input[name="email"]');
    if (await email.getAttribute("maxlength") !== "254") throw new Error("Email does not use the accepted address length.");
    if (await admin.locator('.user-form input[name="roles"]').count()) throw new Error("Owner Administrator form exposes role choices.");
    const nicknameRequired = await admin.getByLabel("Workspace nickname").getAttribute("required");
    if (nicknameRequired === null) throw new Error("Workspace nickname is not required.");
    if (await admin.locator('.user-form input[name="access_id"]').count()) throw new Error("New user form still exposes a permanent access ID.");
    await assertNoOverflow(admin, "desktop Users");
    await admin.screenshot({ path: path.join(artifacts, "admin-desktop.png"), fullPage: true });

    const observedStatement = makeStatement();
    const observedEvidence = makeStatement();
    observedEvidence.id = "stmt-observed-evidence";
    observedEvidence.status = "evidence";
    observedEvidence.evidence = {
      id: "evidence-observed",
      statement_id: observedEvidence.id,
      analysis_id: observedEvidence.analysis.id,
      accepted_at: "2026-07-28T10:00:00+00:00",
    };
    adminStore.review = [observedStatement];
    adminStore.evidence = [observedEvidence];
    await admin.getByLabel("Jump to workspace").selectOption("workspace-admin-alpha");
    await admin.getByText("Viewing Northern pilot - read only", { exact: true }).waitFor();
    for (const tab of ["Review", "Evidence", "Logic model", "Users"]) {
      await admin.getByRole("tab", { name: tab, exact: true }).waitFor();
    }
    if (await admin.getByRole("button", { name: "New user" }).isVisible()) throw new Error("Observer can create a user.");
    if (await admin.locator("#users-list [data-user-action]").count()) throw new Error("Observer can edit or suspend a user.");
    await admin.getByRole("tab", { name: "Logic model" }).click();
    if (await admin.getByRole("button", { name: "Update the Logic Model" }).isVisible()) throw new Error("Observer can update the Logic Model.");
    if (await admin.locator("#logic-version-toolbar").isVisible()) throw new Error("Observer can select or activate a Logic Model version.");
    await admin.getByRole("tab", { name: "Review", exact: true }).click();
    await admin.locator("#review-list").getByText(observedStatement.original_text, { exact: true }).waitFor();
    if (await admin.locator("#review-list [data-action]").count()) throw new Error("Observer can act on a Statement.");
    if (await admin.getByRole("button", { name: "End review" }).isVisible()) throw new Error("Observer can end a review.");
    await admin.getByRole("tab", { name: "Evidence", exact: true }).click();
    await admin.locator("#evidence-list").getByText(observedEvidence.original_text, { exact: true }).waitFor();
    if (await admin.getByRole("button", { name: "Export Evidence PDF" }).isVisible()) throw new Error("Owner observer can export Evidence.");
    await assertNoOverflow(admin, "desktop Owner observer");
    await admin.screenshot({ path: path.join(artifacts, "owner-observer-desktop.png"), fullPage: true });

    if (errors.length) throw new Error(`Browser errors: ${errors.join(" | ")}`);
    console.log("UI smoke test passed at desktop and 360px narrow viewport.");
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
