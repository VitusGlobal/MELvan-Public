const state = {
  user: null,
  activeRole: null,
  activeView: window.location.hash.replace("#", "") || null,
  reviewQueue: "statements",
  review: [],
  evidence: [],
  evidenceCount: 0,
  evidenceReview: [],
  myStatements: [],
  elements: [],
  logicModel: null,
  logicModelVersions: [],
  logicModelDraft: null,
  logicEditing: false,
  users: [],
  quality: { matches: 0, total: 0, percentage: null },
  reviewSession: null,
  reviewReport: null,
  model: null,
  appVersion: null,
  workspaces: [],
  workspaceId: null,
  workspaceNickname: null,
  workspaceReadOnly: false,
  capture: {
    mode: "text",
    voice: { status: "idle", original: "", accepted: false, meta: null },
    photo: { status: "idle", stageId: null, original: "", accepted: false, meta: null, previewUrl: null },
  },
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const RECAP_REFERENCE_URL = "https://www.vitushq.com/en/blog/tired-of-vague-advice-on-writing-a-theory-of-change-narrative-try-the-recap-framework/";
const RECAP_FIELDS = {
  risks: { label: "Risks", prompt: "What could go wrong?" },
  evidence: { label: "Evidence", prompt: "Knowledge, data, or experience" },
  context: { label: "Context", prompt: "Sociopolitical, regulatory, organizational, individual" },
  assumptions: { label: "Assumptions", prompt: "What needs to be true for this to work?" },
  people: { label: "People", prompt: "Who is influencing and influenced by this?" },
};
let loginPending = false;
let logicAutosaveTimer = null;
let voiceStream = null;
let voiceAudioContext = null;
let voiceSource = null;
let voiceProcessor = null;
let voiceSamples = [];
let voiceSampleRate = 48000;
let recordingStartedAt = 0;
let recordingTimer = null;
const MAX_VOICE_SECONDS = 60;
let cameraStream = null;

document.addEventListener("DOMContentLoaded", async () => {
  bindLogin();
  bindShell();
  refreshIcons();
  try {
    const session = await api("/api/session");
    await continueSession(session);
    return;
  } catch {
    clearSession();
  }
  showLogin();
});

function bindLogin() {
  const form = $("#login-form");
  form.addEventListener("submit", submitLogin);
  $("#forgot-password").addEventListener("click", showRecovery);
  $("#recovery-form").addEventListener("submit", submitRecovery);
  $$("[data-return-login]").forEach((button) => button.addEventListener("click", showLogin));
  $("#password-form").addEventListener("submit", submitNewPassword);
  $("#open-user-guide").addEventListener("click", openUserGuide);
  $$("[data-close-user-guide]").forEach((button) => button.addEventListener("click", closeUserGuide));
  $("#user-guide-dialog").addEventListener("cancel", (event) => {
    event.preventDefault();
    closeUserGuide();
  });
}

function openUserGuide() {
  const dialog = $("#user-guide-dialog");
  dialog.showModal();
  refreshIcons();
  $("[data-close-user-guide]", dialog).focus();
}

function closeUserGuide() {
  $("#user-guide-dialog").close();
}

async function submitLogin(event) {
  event.preventDefault();
  if (loginPending || !event.target.reportValidity()) return;
  loginPending = true;
  const status = $("#login-status");
  status.hidden = true;
  const button = event.submitter || $("button[type='submit']", event.target);
  if (button) button.disabled = true;
  try {
    const payload = await api("/api/session", {
      method: "POST",
      body: {
        email: event.target.elements.email.value,
        password: event.target.elements.password.value,
        duration: event.target.elements.duration.value,
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
      },
      anonymous: true,
    });
    event.target.elements.password.value = "";
    await continueSession(payload);
  } catch (error) {
    rejectLogin(error.message);
  } finally {
    loginPending = false;
    if (button) button.disabled = false;
  }
}

function rejectLogin(message = "Unable to complete sign in.") {
  const panel = $(".login-panel");
  const status = $("#login-status");
  status.textContent = message;
  status.hidden = false;
  panel.classList.remove("is-denied");
  void panel.offsetWidth;
  panel.classList.add("is-denied");
  panel.addEventListener("animationend", () => panel.classList.remove("is-denied"), { once: true });
  $("#login-password").focus();
}

function showRecovery() {
  $("#login-screen").hidden = true;
  $("#password-screen").hidden = true;
  $("#role-screen").hidden = true;
  $("#app-shell").hidden = true;
  $("#recovery-screen").hidden = false;
  $("#recovery-status").hidden = true;
  $("#recovery-email").value = $("#login-email").value;
  $("#recovery-email").focus();
}

async function submitRecovery(event) {
  event.preventDefault();
  if (loginPending || !event.target.reportValidity()) return;
  loginPending = true;
  try {
    const payload = await api("/api/auth/recovery", {
      method: "POST",
      body: { email: event.target.elements.email.value },
      anonymous: true,
    });
    $("#recovery-status").textContent = payload.message;
    $("#recovery-status").hidden = false;
  } catch {
    $("#recovery-status").textContent = "Please try again later.";
    $("#recovery-status").hidden = false;
  } finally {
    loginPending = false;
  }
}

function showPasswordChange(payload = {}) {
  const requiresGuide = Boolean(payload.requires_user_guide_acknowledgement);
  const guideAcknowledgement = $("#password-user-guide-acknowledgement");
  const acknowledgement = $("#user-guide-acknowledged");
  guideAcknowledgement.hidden = !requiresGuide;
  acknowledgement.required = requiresGuide;
  acknowledgement.checked = false;
  const guideDialog = $("#user-guide-dialog");
  if (guideDialog.open) guideDialog.close();
  $("#login-screen").hidden = true;
  $("#recovery-screen").hidden = true;
  $("#role-screen").hidden = true;
  $("#app-shell").hidden = true;
  $("#password-screen").hidden = false;
  $("#password-status").hidden = true;
  $("#new-password").focus();
}

async function submitNewPassword(event) {
  event.preventDefault();
  if (loginPending || !event.target.reportValidity()) return;
  loginPending = true;
  const status = $("#password-status");
  status.hidden = true;
  try {
    const payload = await api("/api/session/password", {
      method: "POST",
      body: {
        password: event.target.elements.password.value,
        confirmation: event.target.elements.confirmation.value,
        user_guide_acknowledged: Boolean(
          event.target.elements.user_guide_acknowledged.checked
        ),
      },
    });
    event.target.reset();
    await continueSession(payload);
  } catch (error) {
    status.textContent = error.message;
    status.hidden = false;
  } finally {
    loginPending = false;
  }
}

function bindShell() {
  $("#role-choices").addEventListener("click", assumeRole);
  $("#role-signout").addEventListener("click", signOut);
  $("#workspace-select").addEventListener("change", switchWorkspace);
  $$(".tab").forEach((tab) => tab.addEventListener("click", () => setView(tab.dataset.view)));
  $("#logout").addEventListener("click", signOut);
  $("#report-form").addEventListener("submit", submitStatement);
  $$("[data-capture-mode]").forEach((tab) => tab.addEventListener("click", () => switchCaptureMode(tab.dataset.captureMode)));
  $("#start-recording").addEventListener("click", startVoiceRecording);
  $("#stop-recording").addEventListener("click", stopVoiceRecording);
  $("#retry-voice").addEventListener("click", resetVoiceCapture);
  $("#submit-voice").addEventListener("click", submitVoiceCandidate);
  $("#add-voice-context").addEventListener("click", addVoiceContext);
  $("#voice-text").addEventListener("input", () => markCandidatePending("voice"));
  $("#open-camera").addEventListener("click", openCamera);
  $("#close-camera").addEventListener("click", closeCamera);
  $("#capture-photo").addEventListener("click", captureCameraPhoto);
  $("#choose-photo").addEventListener("click", () => $("#photo-file").click());
  $("#photo-file").addEventListener("change", choosePhoto);
  $("#retry-photo").addEventListener("click", resetPhotoCapture);
  $("#accept-photo").addEventListener("click", acceptPhotoCandidate);
  $("#photo-text").addEventListener("input", () => markCandidatePending("photo"));
  $("#statement-text").addEventListener("input", (event) => {
    $("#character-count").textContent = `${event.target.value.length.toLocaleString()} / 10,000`;
  });
  $("#statement-context").addEventListener("input", (event) => {
    $("#context-character-count").textContent = `${event.target.value.length.toLocaleString()} / 10,000`;
  });
  $("#history-status").addEventListener("change", renderMyStatements);
  $("#history-classification").addEventListener("change", renderMyStatements);
  $("#history-mapping").addEventListener("change", renderMyStatements);
  $("#my-statements-list").addEventListener("click", handleMyStatementsClick);
  $("#edit-context-form").addEventListener("submit", submitContextEdit);
  $("#edit-context-dialog").addEventListener("click", (event) => {
    if (event.target.closest("[data-close-context]")) $("#edit-context-dialog").close();
  });
  $("#edit-context-text").addEventListener("input", (event) => {
    $("#edit-context-count").textContent = `${event.target.value.length.toLocaleString()} / 10,000`;
  });
  $("#refresh-review").addEventListener("click", refreshReviewWorkspace);
  $("#end-review").addEventListener("click", endReview);
  $("#review-classification").addEventListener("change", renderReview);
  $("#review-mapping").addEventListener("change", renderReview);
  $("#review-report").addEventListener("click", (event) => {
    if (!event.target.closest("[data-close-review-report]")) return;
    state.reviewReport = null;
    renderReviewReport();
  });
  $("#review-list").addEventListener("click", handleReviewClick);
  $("#review-list").addEventListener("change", handleReviewChange);
  $("#review-list").addEventListener("submit", handleFeedbackSubmit);
  $$("[data-review-queue]").forEach((button) => {
    button.addEventListener("click", () => setReviewQueue(button.dataset.reviewQueue));
  });
  $("#evidence-review-list").addEventListener("click", handleEvidenceReviewClick);
  $("#evidence-review-list").addEventListener("change", handleEvidenceReviewChange);
  $("#evidence-review-list").addEventListener("submit", handleEvidenceReviewSubmit);
  $("#new-user").addEventListener("click", () => showUserForm());
  $("#users-list").addEventListener("click", handleUserClick);
  $("#user-form-region").addEventListener("submit", submitUserForm);
  $("#user-form-region").addEventListener("change", handleUserFormChange);
  $("#export-evidence").addEventListener("click", exportEvidencePDF);
  $("#reviewer-export-evidence").addEventListener("click", exportEvidencePDF);
  $("#user-form-region").addEventListener("click", (event) => {
    if (event.target.closest("[data-cancel-user]")) $("#user-form-region").hidden = true;
  });
  $("#edit-logic-model").addEventListener("click", startLogicModelEdit);
  $("#logic-model-view").addEventListener("click", handleEmptyLogicModelAction);
  $("#logic-model-editor").addEventListener("input", handleLogicModelInput);
  $("#logic-model-editor").addEventListener("click", handleLogicModelClick);
  $("#logic-version-select").addEventListener("change", previewLogicModel);
  $("#use-logic-version").addEventListener("click", confirmLogicModelActivation);
  window.addEventListener("hashchange", () => {
    const next = window.location.hash.replace("#", "");
    if (allowedViews().has(next)) setView(next, false);
  });
}

async function continueSession(payload) {
  if (payload.requires_password_change) {
    showPasswordChange(payload);
    return;
  }
  state.user = payload.user;
  state.activeRole = payload.active_role || null;
  state.workspaceId = payload.user?.workspace_id || null;
  state.workspaceReadOnly = Boolean(payload.user?.workspace_read_only);
  if (payload.requires_role_selection || !state.activeRole) {
    showRoleChoice();
    return;
  }
  await enterApplication();
}

function showRoleChoice() {
  $("#login-screen").hidden = true;
  $("#recovery-screen").hidden = true;
  $("#password-screen").hidden = true;
  $("#app-shell").hidden = true;
  $("#role-screen").hidden = false;
  const roleMeta = {
    owner: ["Own MELvan", "Manage workspaces and all user access", "shield-check"],
    administrator: ["Administer", "Manage users and the Logic Model", "settings"],
    reporter: ["Report", "Capture and follow your statements", "square-pen"],
    reviewer: ["Review", "Review statements. Submit evidence", "messages-square"],
  };
  $("#role-choices").innerHTML = state.user.roles.map((role) => {
    const [label, detail, icon] = roleMeta[role];
    return `<button class="role-choice" type="button" data-assume-role="${escapeHtml(role)}"><i data-lucide="${icon}" aria-hidden="true"></i><span><strong>${label}</strong><small>${detail}</small></span><i data-lucide="chevron-right" aria-hidden="true"></i></button>`;
  }).join("");
  refreshIcons();
  $("[data-assume-role]")?.focus();
}

async function assumeRole(event) {
  const button = event.target.closest("[data-assume-role]");
  if (!button) return;
  button.disabled = true;
  try {
    const payload = await api("/api/session/role", {
      method: "POST",
      body: { role: button.dataset.assumeRole },
    });
    state.user = payload.user;
    state.activeRole = payload.active_role;
    await enterApplication();
  } catch (error) {
    showToast(error.message, true);
    button.disabled = false;
  }
}

async function enterApplication() {
  $("#login-screen").hidden = true;
  $("#recovery-screen").hidden = true;
  $("#password-screen").hidden = true;
  $("#role-screen").hidden = true;
  $("#app-shell").hidden = false;
  configureAccess();
  setView(firstView(), false);
  await refreshData();
}

function showLogin() {
  $("#app-shell").hidden = true;
  $("#role-screen").hidden = true;
  $("#recovery-screen").hidden = true;
  $("#password-screen").hidden = true;
  $("#login-screen").hidden = false;
  $("#login-status").hidden = true;
  $("#login-password").value = "";
  $("#login-email").focus();
}

async function signOut() {
  try {
    await api("/api/session/logout", { method: "POST", body: {} });
  } catch {
    // Local session is cleared even if the server has already forgotten it.
  }
  clearSession();
  showLogin();
}

function clearSession() {
  state.user = null;
  state.activeRole = null;
  state.activeView = null;
  state.workspaces = [];
  state.workspaceId = null;
  state.workspaceNickname = null;
  state.workspaceReadOnly = false;
}

function roles() {
  return new Set(state.activeRole ? [state.activeRole] : []);
}

function canAdminister() {
  return !state.workspaceReadOnly && (
    roles().has("owner") || roles().has("administrator")
  );
}

function canViewUsers() {
  return canAdminister() || state.workspaceReadOnly;
}

function canViewReviewWorkspace() {
  return roles().has("reviewer") || state.workspaceReadOnly;
}

function canExportEvidence() {
  return !state.workspaceReadOnly && (
    roles().has("administrator") || roles().has("reviewer")
  );
}

function isOwnerHome() {
  return roles().has("owner") && !state.workspaceReadOnly;
}

function allowedViews() {
  const values = new Set(["logic-model"]);
  if (roles().has("reporter")) values.add("report");
  if (roles().has("reporter")) values.add("my-statements");
  if (canViewReviewWorkspace()) {
    values.add("review");
    values.add("evidence");
  }
  if (canViewUsers()) values.add("users");
  return values;
}

function firstView() {
  if (state.workspaceReadOnly) return "review";
  if (roles().has("reporter")) return "report";
  if (roles().has("reviewer")) return "review";
  if (canAdminister()) return "users";
  return "logic-model";
}

function configureAccess() {
  $$(".tab").forEach((tab) => {
    const required = tab.dataset.role;
    const permitted = required === "administrator"
      ? canViewUsers()
      : required === "reviewer"
        ? canViewReviewWorkspace()
        : roles().has(required);
    tab.hidden = Boolean(required && !permitted);
  });
  $("#current-user-name").textContent = `${state.user.first_name} ${state.user.last_name}`;
  $("#current-user-roles").textContent = titleCase(state.activeRole);
  $("#logic-version-toolbar").hidden = state.logicEditing || !canAdminister() || state.logicModelVersions.length <= 1;
  $("#logic-model-admin-action").hidden = !canAdminister() || state.logicEditing || !state.logicModel?.is_current;
  $("#logic-read-only").hidden = canAdminister();
  $("#export-evidence").hidden = !canExportEvidence() || !roles().has("administrator") || state.evidenceCount === 0;
  $("#reviewer-export-evidence").hidden = !canExportEvidence() || !roles().has("reviewer") || state.evidenceCount === 0;
  $("#new-user").hidden = !canAdminister();
  $("#users-eyebrow").textContent = isOwnerHome() ? "Owner" : "Administrator";
  $("#users-title").textContent = isOwnerHome() ? "Administrators" : "Users";
  $("#users-section-heading").classList.toggle("owner-administrators-heading", isOwnerHome());
  $("#users-heading").textContent = isOwnerHome() ? "Administrator workspaces" : "User access";
  $("#users-description").textContent = isOwnerHome() ? "" : "Reporter and Reviewer access";
  $("#new-user-label").textContent = isOwnerHome() ? "Create a new administrator" : "New user";
  const banner = $("#workspace-read-only-banner");
  banner.hidden = !state.workspaceReadOnly;
  $("#workspace-read-only-copy").textContent = state.workspaceReadOnly
    ? `Viewing ${state.workspaceNickname || "Administrator workspace"} - read only`
    : "";
  renderWorkspaceSelector();
}

function setView(view, updateHash = true) {
  if (!allowedViews().has(view)) view = firstView();
  state.activeView = view;
  $$(".tab").forEach((tab) => {
    const active = tab.dataset.view === view;
    tab.classList.toggle("is-active", active);
    tab.setAttribute("aria-selected", String(active));
  });
  $$(".view").forEach((panel) => {
    const active = panel.id === `view-${view}`;
    panel.hidden = !active;
    panel.classList.toggle("is-active", active);
  });
  if (updateHash) history.replaceState(null, "", `#${view}`);
  if (["review", "evidence", "users"].includes(view)) refreshData();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

async function refreshData() {
  if (!state.user) return;
  try {
    const payload = await api("/api/bootstrap");
    state.user = payload.user;
    state.review = payload.review || [];
    state.evidence = payload.evidence || [];
    state.evidenceCount = Number(payload.counts?.evidence || 0);
    state.evidenceReview = payload.evidence_review || [];
    state.myStatements = payload.my_statements || [];
    state.elements = payload.elements || [];
    state.logicModel = payload.logic_model;
    state.logicModelVersions = payload.logic_model_versions || [];
    state.logicModelDraft = payload.logic_model_draft || null;
    state.users = payload.users || [];
    state.quality = payload.quality;
    state.reviewSession = payload.review_session || null;
    state.reviewReport = payload.review_report || null;
    state.model = payload.model;
    state.appVersion = payload.app_version || null;
    state.activeRole = payload.active_role || state.activeRole;
    state.workspaces = payload.workspaces || [];
    state.workspaceId = payload.workspace_id || state.user.workspace_id || null;
    state.workspaceNickname = payload.workspace_nickname || null;
    state.workspaceReadOnly = Boolean(payload.workspace_read_only);
    configureAccess();
    renderAll();
    if (state.activeView === "review" && roles().has("reviewer")) {
      await loadReviewWorkspace();
    }
  } catch (error) {
    setModelStatus(null, error.message);
    showToast(error.message, true);
    if (state.activeView === "review") renderLoadFailure("#review-list", error.message);
    if (state.activeView === "evidence") renderLoadFailure("#evidence-list", error.message);
    if (state.activeView === "logic-model") renderLoadFailure("#logic-model-view", error.message);
  }
}

function renderWorkspaceSelector() {
  const wrapper = $("#workspace-switcher");
  const select = $("#workspace-select");
  const visible = roles().has("owner") && state.workspaces.length > 1;
  wrapper.hidden = !visible;
  if (!visible) return;
  select.innerHTML = state.workspaces.map((workspace) =>
    `<option value="${escapeHtml(workspace.id)}" ${workspace.id === state.workspaceId ? "selected" : ""}>${escapeHtml(workspace.nickname)}</option>`
  ).join("");
}

async function switchWorkspace(event) {
  event.target.disabled = true;
  try {
    const payload = await api("/api/owner/workspace", {
      method: "POST",
      body: { workspace_id: event.target.value },
    });
    state.workspaceId = payload.workspace_id;
    state.logicEditing = false;
    await refreshData();
  } catch (error) {
    showToast(error.message, true);
    renderWorkspaceSelector();
  } finally {
    event.target.disabled = false;
  }
}

function renderAll() {
  updateCounts();
  setModelStatus(state.model);
  renderReview();
  renderEvidenceReview();
  renderEvidence();
  renderMyStatementFilters();
  renderMyStatements();
  renderLogicModel();
  renderLogicModelEditor();
  renderLogicVersions();
  renderUsers();
  renderQuality();
  refreshIcons();
  loadStatementPhotos();
}

function updateCounts() {
  updateCount("#review-count", state.review.length);
  updateCount("#evidence-count", state.evidence.length);
}

function updateCount(selector, count) {
  const element = $(selector);
  if (!element) return;
  element.textContent = count;
  element.hidden = count === 0;
}

function setModelStatus(model, error = null) {
  $("#app-version").textContent = state.appVersion ? `v${state.appVersion}` : "";
  const element = $("#model-state");
  element.hidden = !["localhost", "127.0.0.1"].includes(window.location.hostname);
  if (element.hidden) return;
  element.classList.toggle("is-ready", Boolean(model));
  element.classList.toggle("is-error", Boolean(error));
  element.lastElementChild.textContent = error
    ? "Connection unavailable"
    : model
      ? model.mode === "demo" ? "Demo model ready" : `${model.model} ready`
      : "Connecting";
}

async function switchCaptureMode(mode) {
  if (mode === state.capture.mode) return;
  const current = state.capture[state.capture.mode];
  const hasTransient = state.capture.mode !== "text" && current?.status !== "idle";
  if (hasTransient && !window.confirm("Discard this capture and switch methods? Your Context will be kept.")) return;
  if (state.capture.mode === "voice") resetVoiceCapture();
  if (state.capture.mode === "photo") await resetPhotoCapture();
  state.capture.mode = mode;
  $$("[data-capture-mode]").forEach((tab) => {
    const active = tab.dataset.captureMode === mode;
    tab.classList.toggle("is-active", active);
    tab.setAttribute("aria-selected", String(active));
  });
  ["text", "voice", "photo"].forEach((name) => {
    $(`#capture-${name}-panel`).hidden = name !== mode;
  });
  updateCaptureReadyState();
  const focusTarget = mode === "text" ? $("#statement-text") : mode === "voice" ? $("#start-recording") : $("#open-camera");
  focusTarget.focus();
}

function updateCaptureReadyState() {
  const mode = state.capture.mode;
  let label = "Text entry";
  let ready = true;
  if (mode === "voice") {
    ready = state.capture.voice.accepted;
    label = ready ? "Transcription ready" : "Choose Submit statement or Add context";
  } else if (mode === "photo") {
    ready = state.capture.photo.accepted;
    label = ready ? "Photo text accepted" : "Accept the photo text to submit";
  }
  $("#capture-ready-status").textContent = label;
  $("#submit-statement").disabled = !ready;
}

function markCandidatePending(mode) {
  if (state.capture[mode].accepted) {
    state.capture[mode].accepted = false;
    state.capture[mode].status = "candidate_ready";
    $(`#${mode}-status`).hidden = true;
  }
  updateCaptureReadyState();
}

function showCaptureError(target, message) {
  const status = $(target);
  status.textContent = message;
  status.classList.add("is-error");
  status.hidden = false;
}

async function startVoiceRecording() {
  $("#voice-status").hidden = true;
  if (!navigator.mediaDevices?.getUserMedia || !(window.AudioContext || window.webkitAudioContext)) {
    showCaptureError("#voice-status", "Microphone recording is not available in this browser. Allow microphone access and try again.");
    $("#start-recording span").textContent = "Try again";
    return;
  }
  try {
    voiceStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    voiceAudioContext = new AudioContextClass();
    voiceSampleRate = voiceAudioContext.sampleRate;
    voiceSamples = [];
    voiceSource = voiceAudioContext.createMediaStreamSource(voiceStream);
    voiceProcessor = voiceAudioContext.createScriptProcessor(4096, 1, 1);
    voiceProcessor.onaudioprocess = (event) => {
      voiceSamples.push(new Float32Array(event.inputBuffer.getChannelData(0)));
    };
    voiceSource.connect(voiceProcessor);
    voiceProcessor.connect(voiceAudioContext.destination);
    recordingStartedAt = Date.now();
    state.capture.voice.status = "capturing";
    $("#voice-idle").hidden = true;
    $("#voice-recording").hidden = false;
    updateRecordingTime();
    recordingTimer = window.setInterval(updateRecordingTime, 1000);
  } catch {
    showCaptureError("#voice-status", "Allow MELvan to use your microphone, then select Try again.");
    $("#start-recording span").textContent = "Try again";
  }
}

function updateRecordingTime() {
  const seconds = Math.min(MAX_VOICE_SECONDS, Math.floor((Date.now() - recordingStartedAt) / 1000));
  $("#recording-time").textContent = `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
  if (seconds >= MAX_VOICE_SECONDS) finishVoiceRecording(true);
}

function stopVoiceRecording() {
  if (state.capture.voice.status === "capturing") finishVoiceRecording(false);
}

async function finishVoiceRecording(limitReached = false) {
  if (state.capture.voice.status !== "capturing") return;
  state.capture.voice.status = "processing";
  window.clearInterval(recordingTimer);
  recordingTimer = null;
  $("#voice-recording").hidden = true;
  $("#voice-processing").hidden = false;
  $("#voice-processing-message").textContent = limitReached
    ? "60-second limit reached. Transcribing your statement..."
    : "Transcribing your statement...";
  voiceProcessor?.disconnect();
  voiceSource?.disconnect();
  voiceProcessor = null;
  voiceSource = null;
  await voiceAudioContext?.close();
  voiceAudioContext = null;
  stopTracks(voiceStream);
  voiceStream = null;
  const blob = encodeWav(voiceSamples, voiceSampleRate);
  voiceSamples = [];
  try {
    if (blob.size > 2_100_000) throw new Error("The recording is too large. Record a shorter statement.");
    const payload = await api("/api/media/voice/transcribe", {
      method: "POST",
      body: { data_base64: await blobToBase64(blob), media_type: blob.type },
    });
    const result = payload.transcription;
    state.capture.voice = { status: "candidate_ready", original: result.text, accepted: false, meta: result };
    $("#voice-text").value = result.text;
    $("#voice-candidate").hidden = false;
    if (limitReached) {
      $("#voice-status").classList.remove("is-error");
      $("#voice-status").textContent = "The 60-second recording limit was reached. Review the transcription below.";
      $("#voice-status").hidden = false;
    }
    $("#voice-text").focus();
  } catch (error) {
    resetVoiceCapture();
    showCaptureError("#voice-status", error.message);
    $("#start-recording span").textContent = "Try again";
  } finally {
    $("#voice-processing").hidden = true;
    updateCaptureReadyState();
  }
}

function prepareVoiceCandidate() {
  const value = $("#voice-text").value.trim();
  if (!value) {
    showCaptureError("#voice-status", "The transcription is empty. Try recording again.");
    return false;
  }
  state.capture.voice.accepted = true;
  state.capture.voice.status = "candidate_accepted";
  updateCaptureReadyState();
  return true;
}

function submitVoiceCandidate() {
  if (!prepareVoiceCandidate()) return;
  $("#report-form").requestSubmit($("#submit-statement"));
}

function addVoiceContext() {
  if (!prepareVoiceCandidate()) return;
  $("#voice-status").classList.remove("is-error");
  $("#voice-status").textContent = "Add any useful context, then submit the Statement.";
  $("#voice-status").hidden = false;
  $("#statement-context").focus();
}

function resetVoiceCapture() {
  voiceProcessor?.disconnect();
  voiceSource?.disconnect();
  voiceProcessor = null;
  voiceSource = null;
  voiceAudioContext?.close();
  voiceAudioContext = null;
  stopTracks(voiceStream);
  voiceStream = null;
  voiceSamples = [];
  window.clearInterval(recordingTimer);
  recordingTimer = null;
  state.capture.voice = { status: "idle", original: "", accepted: false, meta: null };
  $("#voice-idle").hidden = false;
  $("#voice-recording").hidden = true;
  $("#voice-processing").hidden = true;
  $("#voice-candidate").hidden = true;
  $("#voice-status").hidden = true;
  $("#voice-text").value = "";
  $("#start-recording span").textContent = "Record statement";
  updateCaptureReadyState();
}

async function openCamera() {
  $("#photo-status").hidden = true;
  if (!navigator.mediaDevices?.getUserMedia) {
    showCaptureError("#photo-status", "Camera access is unavailable. Choose a photo from your device instead.");
    return;
  }
  try {
    cameraStream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: { ideal: "environment" } },
      audio: false,
    });
    const video = $("#camera-video");
    const takePhoto = $("#capture-photo");
    takePhoto.disabled = true;
    video.srcObject = cameraStream;
    $("#photo-idle").hidden = true;
    $("#camera-preview").hidden = false;
    state.capture.photo.status = "capturing";
    await video.play();
    if (!video.videoWidth || !video.videoHeight) {
      await waitForCameraFrame(video);
    }
    takePhoto.disabled = false;
  } catch {
    closeCamera();
    showCaptureError("#photo-status", "Camera preview could not start. Allow MELvan to use your camera, or choose a photo from your device.");
  }
}

function waitForCameraFrame(video) {
  return new Promise((resolve, reject) => {
    const timeout = window.setTimeout(() => finish(false), 5000);
    const finish = (ready) => {
      window.clearTimeout(timeout);
      video.removeEventListener("loadeddata", onReady);
      video.removeEventListener("error", onError);
      ready ? resolve() : reject(new Error("Camera preview unavailable"));
    };
    const onReady = () => finish(Boolean(video.videoWidth && video.videoHeight));
    const onError = () => finish(false);
    video.addEventListener("loadeddata", onReady);
    video.addEventListener("error", onError);
  });
}

function closeCamera() {
  stopTracks(cameraStream);
  cameraStream = null;
  const video = $("#camera-video");
  video.pause();
  video.srcObject = null;
  $("#capture-photo").disabled = true;
  $("#camera-preview").hidden = true;
  $("#photo-idle").hidden = false;
  if (!state.capture.photo.stageId) state.capture.photo.status = "idle";
}

function captureCameraPhoto() {
  const video = $("#camera-video");
  if (!video.videoWidth || !video.videoHeight) {
    showCaptureError("#photo-status", "The camera is not ready. Try again.");
    return;
  }
  const canvas = document.createElement("canvas");
  canvas.width = video.videoWidth;
  canvas.height = video.videoHeight;
  canvas.getContext("2d").drawImage(video, 0, 0);
  canvas.toBlob((blob) => {
    closeCamera();
    if (blob) processPhoto(blob, "camera");
  }, "image/jpeg", 0.9);
}

function choosePhoto(event) {
  const file = event.target.files?.[0];
  event.target.value = "";
  if (file) processPhoto(file, "library");
}

async function processPhoto(file, source) {
  if (file.size > 10 * 1024 * 1024) {
    showCaptureError("#photo-status", "The photo must be 10 MB or smaller.");
    return;
  }
  await resetPhotoCapture(false);
  state.capture.photo.status = "processing";
  const previewUrl = URL.createObjectURL(file);
  state.capture.photo.previewUrl = previewUrl;
  $("#photo-preview-image").src = previewUrl;
  $("#photo-preview").hidden = false;
  $("#photo-idle").hidden = true;
  $("#photo-processing").hidden = false;
  try {
    const payload = await api("/api/media/photo/stage", {
      method: "POST",
      body: { data_base64: await blobToBase64(file), source },
    });
    const result = payload.photo;
    state.capture.photo = {
      status: "candidate_ready",
      stageId: result.stage_id,
      original: result.candidate_text,
      accepted: false,
      meta: result,
      previewUrl,
    };
    $("#photo-text").value = result.candidate_text;
    $("#photo-no-text-guidance").hidden = result.outcome !== "no_text";
    $("#photo-candidate").hidden = false;
    $("#photo-text").focus();
  } catch (error) {
    await resetPhotoCapture();
    showCaptureError("#photo-status", error.message);
  } finally {
    $("#photo-processing").hidden = true;
    updateCaptureReadyState();
  }
}

function acceptPhotoCandidate() {
  const value = $("#photo-text").value.trim();
  if (!value) {
    showCaptureError("#photo-status", "Enter a Statement before using this photo.");
    return;
  }
  state.capture.photo.accepted = true;
  state.capture.photo.status = "candidate_accepted";
  $("#photo-status").classList.remove("is-error");
  $("#photo-status").textContent = "Photo text accepted. You can now submit the Statement.";
  $("#photo-status").hidden = false;
  updateCaptureReadyState();
}

async function resetPhotoCapture(discardRemote = true) {
  const stageId = state.capture.photo.stageId;
  if (discardRemote && stageId) {
    try {
      await api(`/api/media/photo/${encodeURIComponent(stageId)}/discard`, { method: "POST", body: {} });
    } catch {
      // The staging timeout and session cleanup remain the server-side fallback.
    }
  }
  closeCamera();
  if (state.capture.photo.previewUrl) URL.revokeObjectURL(state.capture.photo.previewUrl);
  state.capture.photo = { status: "idle", stageId: null, original: "", accepted: false, meta: null, previewUrl: null };
  $("#photo-idle").hidden = false;
  $("#photo-preview").hidden = true;
  $("#photo-processing").hidden = true;
  $("#photo-candidate").hidden = true;
  $("#photo-status").hidden = true;
  $("#photo-text").value = "";
  $("#photo-preview-image").removeAttribute("src");
  updateCaptureReadyState();
}

function stopTracks(stream) {
  if (stream) stream.getTracks().forEach((track) => track.stop());
}

function blobToBase64(blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.addEventListener("load", () => resolve(String(reader.result).split(",", 2)[1] || ""));
    reader.addEventListener("error", () => reject(new Error("The media could not be read.")));
    reader.readAsDataURL(blob);
  });
}

function encodeWav(chunks, sourceRate) {
  const total = chunks.reduce((sum, chunk) => sum + chunk.length, 0);
  const input = new Float32Array(total);
  let offset = 0;
  chunks.forEach((chunk) => {
    input.set(chunk, offset);
    offset += chunk.length;
  });
  const targetRate = 16000;
  const ratio = sourceRate / targetRate;
  const samples = new Int16Array(Math.max(
    1,
    Math.min(MAX_VOICE_SECONDS * targetRate, Math.floor(input.length / ratio)),
  ));
  for (let index = 0; index < samples.length; index += 1) {
    const start = Math.floor(index * ratio);
    const end = Math.min(input.length, Math.floor((index + 1) * ratio));
    let sum = 0;
    for (let source = start; source < end; source += 1) sum += input[source];
    const value = Math.max(-1, Math.min(1, sum / Math.max(1, end - start)));
    samples[index] = value < 0 ? value * 0x8000 : value * 0x7fff;
  }
  const buffer = new ArrayBuffer(44 + samples.byteLength);
  const view = new DataView(buffer);
  const write = (position, value) => [...value].forEach((character, index) => view.setUint8(position + index, character.charCodeAt(0)));
  write(0, "RIFF");
  view.setUint32(4, 36 + samples.byteLength, true);
  write(8, "WAVE");
  write(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, targetRate, true);
  view.setUint32(28, targetRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  write(36, "data");
  view.setUint32(40, samples.byteLength, true);
  new Int16Array(buffer, 44).set(samples);
  return new Blob([buffer], { type: "audio/wav" });
}

async function submitStatement(event) {
  event.preventDefault();
  const mode = state.capture.mode;
  const captureState = mode === "text" ? null : state.capture[mode];
  const text = mode === "text"
    ? $("#statement-text").value
    : mode === "voice" ? $("#voice-text").value : $("#photo-text").value;
  const context = $("#statement-context").value;
  const button = $("#submit-statement");
  const status = $("#report-status");
  button.disabled = true;
  status.hidden = false;
  status.classList.remove("is-error");
  status.textContent = "Analyzing statement...";
  try {
    if (mode !== "text" && !captureState.accepted) throw new Error(`Accept the ${mode === "voice" ? "transcription" : "photo text"} before submitting.`);
    const capture = mode === "text" ? { method: "text" } : {
      method: mode,
      stage_id: captureState.stageId,
      candidate_text: captureState.original,
      edited: text.trim() !== captureState.original.trim(),
      provider: captureState.meta.provider,
      provider_version: captureState.meta.provider_version,
      processing_ms: captureState.meta.processing_ms,
      outcome: captureState.meta.outcome,
    };
    await api("/api/statements", { method: "POST", body: { text, context, capture } });
    $("#statement-text").value = "";
    $("#statement-context").value = "";
    $("#character-count").textContent = "0 / 10,000";
    $("#context-character-count").textContent = "0 / 10,000";
    if (mode === "voice") resetVoiceCapture();
    if (mode === "photo") await resetPhotoCapture(false);
    await switchCaptureMode("text");
    status.textContent = "Statement captured and ready for review.";
    await refreshData();
  } catch (error) {
    status.classList.add("is-error");
    status.textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

function renderMyStatementFilters() {
  const filters = $("#history-filters");
  filters.hidden = state.myStatements.length === 0;
  if (filters.hidden) return;

  const statusSelect = $("#history-status");
  const selectedStatus = statusSelect.value;
  const hasStatements = state.myStatements.some((statement) => statement.status !== "evidence");
  const hasEvidence = state.myStatements.some((statement) => statement.status === "evidence");
  statusSelect.innerHTML = `<option value="">All records</option>${hasStatements ? '<option value="statement">Open statements</option>' : ""}${hasEvidence ? '<option value="evidence">Accepted evidence</option>' : ""}`;
  if ([...statusSelect.options].some((option) => option.value === selectedStatus)) statusSelect.value = selectedStatus;

  const classificationSelect = $("#history-classification");
  const selectedClassification = classificationSelect.value;
  const classifications = [...new Set(state.myStatements.map(
    (statement) => statement.analysis?.classification || statement.approved_classification
  ).filter(Boolean))].sort((a, b) => a.localeCompare(b));
  classificationSelect.innerHTML = `<option value="">All classifications</option>${classifications.map(
    (classification) => `<option value="${escapeHtml(classification)}">${escapeHtml(classification)}</option>`
  ).join("")}`;
  if ([...classificationSelect.options].some((option) => option.value === selectedClassification)) classificationSelect.value = selectedClassification;
  $("#history-classification-field").hidden = classifications.length === 0;

  const select = $("#history-mapping");
  const selected = select.value;
  const groups = { Output: new Map(), Outcome: new Map() };
  state.myStatements.forEach((statement) => {
    (statement.mappings || []).forEach((mapping) => {
      if (!groups[mapping.element_type]) return;
      const key = mapping.stable_id || mapping.id;
      if (!groups[mapping.element_type].has(key)) {
        groups[mapping.element_type].set(key, mapping.label);
      }
    });
  });
  const options = Object.entries(groups).map(([type, values]) => {
    if (!values.size) return "";
    const entries = [...values.entries()]
      .sort((a, b) => a[1].localeCompare(b[1]))
      .map(([value, label]) => `<option value="${escapeHtml(value)}">${escapeHtml(label)}</option>`)
      .join("");
    return `<optgroup label="${type}s">${entries}</optgroup>`;
  }).join("");
  select.innerHTML = `<option value="">All mapped elements</option>${options}`;
  if ([...select.options].some((option) => option.value === selected)) select.value = selected;
  $("#history-mapping-field").hidden = !Object.values(groups).some((values) => values.size);
}

function renderMyStatements() {
  const root = $("#my-statements-list");
  if (!roles().has("reporter")) {
    root.innerHTML = "";
    $("#history-record-count").textContent = "";
    return;
  }
  const status = $("#history-status").value;
  const classification = $("#history-classification").value;
  const mapping = $("#history-mapping").value;
  const records = state.myStatements.filter((statement) => {
    const statusMatch = !status || (status === "evidence" ? statement.status === "evidence" : statement.status !== "evidence");
    const recordClassification = statement.analysis?.classification || statement.approved_classification;
    const classificationMatch = !classification || recordClassification === classification;
    const mappingMatch = !mapping || (statement.mappings || []).some(
      (item) => (item.stable_id || item.id) === mapping
    );
    return statusMatch && classificationMatch && mappingMatch;
  });
  const hasActiveFilter = Boolean(status || classification || mapping);
  const total = state.myStatements.length;
  $("#history-record-count").textContent = hasActiveFilter
    ? `${records.length} of ${total} ${total === 1 ? "record" : "records"} displayed with this filter`
    : `${total} ${total === 1 ? "record" : "records"}`;
  if (!records.length) {
    root.innerHTML = emptyState(
      "inbox",
      state.myStatements.length
        ? "No statements match these filters."
        : "You have not submitted any statements yet."
    );
    refreshIcons();
    return;
  }
  root.innerHTML = records.map(myStatementCard).join("");
  refreshIcons();
  loadStatementPhotos();
}

function myStatementCard(statement) {
  const status = statement.status === "evidence" ? "Evidence" : "Statement";
  const stateLabel = ({ processing: "Processing", awaiting_review: "Awaiting review", failed: "Analysis failed", evidence: "Accepted" })[statement.status] || titleCase(statement.status);
  const edit = statement.status === "evidence" ? "" : `<button class="button" type="button" data-edit-context><i data-lucide="pencil" aria-hidden="true"></i><span>Edit Context</span></button>`;
  const failure = statement.processing_error
    ? `<div class="inline-status is-error">${escapeHtml(statement.processing_error)}</div>` : "";
  const feedback = (statement.review_feedback || []).filter((item) => item.comments).map(
    (item) => `<div class="history-feedback"><h3>Reviewer feedback</h3><p>${escapeHtml(item.comments)}</p><small>${escapeHtml(formatDate(item.created_at))}</small></div>`
  ).join("");
  const dates = statement.evidence
    ? `Submitted ${escapeHtml(formatDate(statement.submitted_at))}<br>Approved ${escapeHtml(formatDate(statement.evidence.accepted_at))}`
    : escapeHtml(formatDate(statement.submitted_at));
  return `<article class="record-card history-card" data-statement-id="${escapeHtml(statement.id)}">
    <div class="record-main">
      <div class="record-topline"><div><div class="history-status"><span class="tag">${status}</span><span class="tag tag-neutral">${stateLabel}</span></div><p class="statement-text">${escapeHtml(statement.original_text)}</p></div><span class="record-meta">${dates}</span></div>
      ${captureProvenanceView(statement)}
      ${contextView(statement)}
      ${statementPhotoView(statement)}
      ${failure}
      ${statement.analysis ? analysisView(statement) : ""}
      ${feedback}
    </div>
    ${edit ? `<div class="record-actions">${edit}</div>` : ""}
  </article>`;
}

function contextView(statement) {
  const value = statement.context?.text || "";
  return `<div class="statement-context"><div><h3>Context</h3><span>${value ? `Updated ${escapeHtml(formatDate(statement.context.created_at))}` : "No Context provided"}</span></div>${value ? `<p>${escapeHtml(value)}</p>` : ""}</div>`;
}

function captureProvenanceView(statement) {
  const method = statement.capture_method || "text";
  if (method === "text") return "";
  const edited = statement.capture_text_edited ? " · Reporter edited the captured text" : "";
  return `<p class="capture-provenance"><i data-lucide="${method === "voice" ? "mic" : "camera"}" aria-hidden="true"></i>${method === "voice" ? "Voice transcription" : "Photo text"}${edited}</p>`;
}

function statementPhotoView(statement) {
  if (!statement.attachment) return "";
  return `<figure class="statement-photo"><img data-photo-url="${escapeHtml(statement.attachment.content_url)}" alt="Photo attached to this Statement" /><figcaption>Photo Statement · ${escapeHtml(statement.attachment.width)} × ${escapeHtml(statement.attachment.height)} px</figcaption></figure>`;
}

async function loadStatementPhotos() {
  const images = $$("img[data-photo-url]:not([data-photo-loaded])");
  await Promise.all(images.map(async (image) => {
    image.dataset.photoLoaded = "loading";
    try {
      const payload = await api(image.dataset.photoUrl);
      image.src = `data:${payload.media_type};base64,${payload.data_base64}`;
      image.dataset.photoLoaded = "true";
    } catch {
      image.closest(".statement-photo")?.remove();
    }
  }));
}

function handleMyStatementsClick(event) {
  const button = event.target.closest("[data-edit-context]");
  if (!button) return;
  const id = button.closest("[data-statement-id]").dataset.statementId;
  const statement = state.myStatements.find((item) => item.id === id);
  if (!statement || statement.status === "evidence") return;
  const dialog = $("#edit-context-dialog");
  dialog.dataset.statementId = statement.id;
  $("#context-statement-reference").textContent = statement.original_text;
  $("#edit-context-text").value = statement.context?.text || "";
  $("#edit-context-count").textContent = `${$("#edit-context-text").value.length.toLocaleString()} / 10,000`;
  dialog.showModal();
  $("#edit-context-text").focus();
  refreshIcons();
}

async function submitContextEdit(event) {
  event.preventDefault();
  const dialog = $("#edit-context-dialog");
  const button = $("button[type='submit']", event.target);
  button.disabled = true;
  const label = $("span", button);
  const previousLabel = label.textContent;
  label.textContent = "Analyzing...";
  try {
    await api(`/api/statements/${encodeURIComponent(dialog.dataset.statementId)}/context`, {
      method: "POST",
      body: { context: $("#edit-context-text").value },
    });
    dialog.close();
    showToast("Context updated and the latest analysis is ready for review.");
    await refreshData();
  } catch (error) {
    showToast(error.message, true);
    await refreshData();
  } finally {
    button.disabled = false;
    label.textContent = previousLabel;
  }
}

function setReviewQueue(queue) {
  if (queue === "evidence" && !state.evidenceReview.length) queue = "statements";
  state.reviewQueue = queue;
  syncReviewQueueTabs();
  renderReview();
  renderEvidenceReview();
}

function syncReviewQueueTabs() {
  const evidenceTab = $('[data-review-queue="evidence"]');
  evidenceTab.hidden = state.evidenceReview.length === 0;
  if (evidenceTab.hidden && state.reviewQueue === "evidence") {
    state.reviewQueue = "statements";
  }
  $$("[data-review-queue]").forEach((button) => {
    const active = button.dataset.reviewQueue === state.reviewQueue;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-selected", String(active));
  });
  $("#statement-review-panel").hidden = state.reviewQueue !== "statements";
  $("#evidence-review-panel").hidden = state.reviewQueue !== "evidence";
}

async function loadReviewWorkspace() {
  if (!state.user || !roles().has("reviewer")) return;
  try {
    const [payload, evidencePayload] = await Promise.all([
      api("/api/review"),
      api("/api/evidence-assessments"),
    ]);
    state.review = payload.statements || [];
    state.evidenceReview = evidencePayload.assessments || [];
    state.reviewSession = payload.session || null;
    state.reviewReport = payload.report || null;
    updateCounts();
    renderReview();
    refreshIcons();
  } catch (error) {
    renderLoadFailure("#review-list", error.message);
    showToast(error.message, true);
  }
}

async function refreshReviewWorkspace() {
  if (state.workspaceReadOnly) {
    await refreshData();
    return;
  }
  await loadReviewWorkspace();
}

async function endReview() {
  if (state.workspaceReadOnly) return;
  if (!state.reviewSession) return;
  if (!window.confirm("End this review? Untouched statements will remain available for a later review.")) return;
  const button = $("#end-review");
  button.disabled = true;
  try {
    const payload = await api("/api/review/end", { method: "POST", body: {} });
    state.reviewSession = null;
    state.reviewReport = payload.report;
    renderReview();
    window.scrollTo({ top: 0, behavior: "smooth" });
  } catch (error) {
    showToast(error.message, true);
  } finally {
    button.disabled = false;
  }
}

function renderReview() {
  const root = $("#review-list");
  syncReviewQueueTabs();
  renderReviewFilters();
  renderReviewReport();
  $("#end-review").hidden = state.workspaceReadOnly || !state.reviewSession;
  const classification = $("#review-classification").value;
  const mapping = $("#review-mapping").value;
  const statements = state.review.filter((statement) => {
    if (classification && statement.analysis?.classification !== classification) return false;
    if (mapping && !statement.mappings.some((item) => item.id === mapping)) return false;
    return true;
  });
  $("#statement-review-count").textContent = state.review.length;
  $("#evidence-review-count").textContent = state.evidenceReview.length;
  const total = state.review.length;
  if (state.reviewQueue === "evidence") {
    $("#review-record-count").textContent = `${state.evidenceReview.length} ${state.evidenceReview.length === 1 ? "record" : "records"}`;
    return;
  }
  $("#review-record-count").textContent = classification || mapping
    ? `${statements.length} of ${total} ${total === 1 ? "statement" : "statements"} showing for this filter`
    : `${total} ${total === 1 ? "statement" : "statements"}`;
  if (!total) {
    root.innerHTML = emptyState("inbox", "There are no statements awaiting review.");
    return;
  }
  if (!statements.length) {
    root.innerHTML = emptyState("filter-x", "No statements match these filters.");
    return;
  }
  root.innerHTML = statements.map((statement) => reviewCard(statement)).join("");
  loadStatementPhotos();
}

function renderReviewFilters() {
  const filters = $("#review-filters");
  filters.hidden = state.reviewQueue !== "statements" || state.review.length < 2;
  if (filters.hidden) return;
  const classificationSelect = $("#review-classification");
  const mappingSelect = $("#review-mapping");
  const currentClassification = classificationSelect.value;
  const currentMapping = mappingSelect.value;
  const classifications = [...new Set(state.review.map((item) => item.analysis?.classification).filter(Boolean))]
    .sort((a, b) => a.localeCompare(b));
  const mappings = new Map();
  state.review.forEach((statement) => statement.mappings.forEach((mapping) => {
    if (mapping.status !== "provisional") mappings.set(mapping.id, mapping.label);
  }));
  classificationSelect.innerHTML = `<option value="">All classifications</option>${classifications.map((value) => `<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`).join("")}`;
  mappingSelect.innerHTML = `<option value="">All mapped elements</option>${[...mappings.entries()].sort((a, b) => a[1].localeCompare(b[1])).map(([id, label]) => `<option value="${escapeHtml(id)}">${escapeHtml(label)}</option>`).join("")}`;
  classificationSelect.value = classifications.includes(currentClassification) ? currentClassification : "";
  mappingSelect.value = mappings.has(currentMapping) ? currentMapping : "";
}

function renderReviewReport() {
  const root = $("#review-report");
  const report = state.reviewReport;
  root.hidden = !report;
  if (!report) {
    root.innerHTML = "";
    return;
  }
  const scoreLabels = {
    first_review_approval: "First-review approval",
    correct_level: "Correct level",
    correct_direction: "Correct direction",
    correct_mapping: "Correct mapping",
    overall_hygiene: "No hallucination/toxic content",
  };
  const scoreGrid = (scores) => Object.entries(scoreLabels).map(([key, label]) => {
    const score = scores[key] || { matches: 0, total: 0, percentage: null };
    return `<div class="score-cell"><span>${label}</span><strong>${score.percentage == null ? "—" : `${score.percentage}%`}</strong><small>${score.matches} of ${score.total}</small></div>`;
  }).join("");
  const trend = report.session_trend || [];
  const recent = trend.slice(-3);
  const trendStatus = Object.entries(scoreLabels).map(([key, label]) => {
    const total = recent.reduce((sum, session) => sum + (session.scores[key]?.total || 0), 0);
    return `<div class="trend-status"><span>${label}</span><strong>${total >= 20 ? "Trend available" : "Insufficient sample"}</strong><small>${total} eligible events in the latest ${recent.length} session${recent.length === 1 ? "" : "s"}</small></div>`;
  }).join("");
  const trendRows = trend.map((session, index) => `<tr>
    <th scope="row">${index + 1}<small>${escapeHtml(formatDate(session.completed_at))}</small></th>
    <td><span class="memory-state ${session.memory_enabled ? "is-enabled" : ""}">${session.memory_enabled ? "Enabled" : "Disabled"}</span><small>${escapeHtml(session.learning_policy_version)}</small></td>
    ${Object.keys(scoreLabels).map((key) => {
      const score = session.scores[key] || { matches: 0, total: 0, percentage: null };
      const value = score.percentage == null ? 0 : score.percentage;
      return `<td><div class="trend-value"><span class="trend-bar"><i style="width:${value}%"></i></span><strong>${score.percentage == null ? "—" : `${score.percentage}%`}</strong></div><small>${score.matches} of ${score.total}</small></td>`;
    }).join("")}
  </tr>`).join("");
  const trendSection = trend.length ? `<div class="score-section trend-section"><h3>Review-session trend</h3><div class="trend-status-grid">${trendStatus}</div><div class="trend-table-wrap"><table class="trend-table"><thead><tr><th>Session</th><th>Learning memory</th>${Object.values(scoreLabels).map((label) => `<th>${label}</th>`).join("")}</tr></thead><tbody>${trendRows}</tbody></table></div></div>` : "";
  const diagnostics = report.diagnostics || {
    omissions: 0,
    unsupported_mappings: 0,
    high_confidence_errors: 0,
    tentative_mappings_presented: 0,
    tentative_mappings_accepted: 0,
    tentative_mappings_rejected: 0,
  };
  root.innerHTML = `
    <div class="review-report-heading"><div><p class="eyebrow">Review complete</p><h2>How did MELvan do?</h2></div><button class="icon-button" type="button" data-close-review-report aria-label="Close review report" title="Close"><i data-lucide="x" aria-hidden="true"></i></button></div>
    <div class="review-totals"><div><strong>${report.counts.reviewed}</strong><span>Reviewed</span></div><div><strong>${report.counts.accepted}</strong><span>Accepted unchanged</span></div><div><strong>${report.counts.sent_back}</strong><span>Changed</span></div><div><strong>${report.counts.not_relevant}</strong><span>Not relevant</span></div></div>
    <div class="score-section"><h3>This review</h3><div class="score-grid">${scoreGrid(report.session_scores)}</div></div>
    <div class="score-section"><h3>MELvan lifetime score</h3><div class="score-grid">${scoreGrid(report.lifetime_scores)}</div></div>
    <div class="learning-diagnostics" aria-label="Learning diagnostics"><span><strong>${diagnostics.omissions}</strong> omissions corrected</span><span><strong>${diagnostics.unsupported_mappings}</strong> unsupported mappings removed</span><span title="A mapping MELvan rated at least 80% confidence that a Reviewer later corrected or removed."><strong>${diagnostics.high_confidence_errors}</strong> high-confidence errors <small>Mappings rated at least 80% that Reviewers corrected or removed</small></span><span><strong>${diagnostics.tentative_mappings_presented}</strong> low-confidence mappings <small>${diagnostics.tentative_mappings_accepted} accepted · ${diagnostics.tentative_mappings_rejected} rejected</small></span></div>
    ${trendSection}`;
  refreshIcons();
}

function reviewCard(statement) {
  const noSuitableMatch = statement.analysis?.match_status === "no_suitable_match";
  const actions = state.workspaceReadOnly ? "" : `
      <div class="record-actions">
        <button class="button feedback-toggle" type="button" data-action="feedback"><i data-lucide="pencil" aria-hidden="true"></i><span>${noSuitableMatch ? "Change classification" : "Change classification/mapping"}</span></button>
        <button class="button button-danger-subtle" type="button" data-action="not-relevant"><i data-lucide="circle-slash" aria-hidden="true"></i><span>Not relevant</span></button>
        ${noSuitableMatch ? "" : `<button class="button button-primary" type="button" data-action="accept"><i data-lucide="check" aria-hidden="true"></i><span>Accept</span></button>`}
      </div>
      ${feedbackPanel(statement)}`;
  return `
    <article class="record-card" data-statement-id="${escapeHtml(statement.id)}">
      <div class="record-main">
        <div class="record-topline">
          <div><p class="eyebrow">Statement</p><p class="statement-text">${escapeHtml(statement.original_text)}</p></div>
          <span class="record-meta">${escapeHtml(formatDate(statement.submitted_at))}</span>
        </div>
        ${captureProvenanceView(statement)}
        ${contextView(statement)}
        ${statementPhotoView(statement)}
        ${analysisView(statement)}
      </div>
      ${actions}
    </article>`;
}

function analysisView(statement) {
  const analysis = statement.analysis;
  if (!analysis) return `<div class="analysis-section"><p class="analysis-summary">Analysis is unavailable.</p></div>`;
  const outcome = analysis.classification === "Outcome"
    ? `<span class="tag">Category · ${escapeHtml(analysis.outcome_change_types.join(", "))}</span>` : "";
  const direction = analysis.result_direction
    ? `<span class="tag ${analysis.result_direction === "Negative" ? "tag-negative" : "tag-positive"}">${escapeHtml(analysis.result_direction)}</span>` : "";
  return `
    <div class="analysis-section">
      <div class="analysis-header"><span class="tag">${escapeHtml(analysis.classification)}</span>${direction}${outcome}<span class="tag tag-confidence">${percent(analysis.classification_confidence)} confidence</span></div>
      <p class="analysis-summary">${escapeHtml(analysis.summary)}</p>
      <p class="analysis-provenance">Logic Model ${escapeHtml(analysis.logic_model_version)}</p>
      <div class="analysis-grid">
        <div><h3>${statement.proposed_element ? "Proposed element" : "Logic Model mapping"}</h3><div class="mapping-list">${mappingList(statement)}</div>${analysis.match_status === "no_suitable_match" && analysis.mapping_rationale ? `<p class="mapping-rationale">${escapeHtml(analysis.mapping_rationale)}</p>` : ""}${analysis.match_status === "no_suitable_match" ? `<p class="mapping-guidance">Consider updating the logic model if you think a new output or outcome is necessary.</p>` : ""}</div>
        <div class="rationale-list"><div><h3>Classification rationale</h3><p>${escapeHtml(analysis.classification_rationale)}</p></div></div>
      </div>
    </div>`;
}

function mappingList(statement) {
  if (!statement.mappings.length) return `<div class="mapping-item"><span class="mapping-label">No suitable existing match</span></div>`;
  return statement.mappings.map((mapping) => {
    const proposed = mapping.status === "provisional";
    const sourceLabel = proposed ? "MELvan proposal" : mapping.source === "reviewer" ? "Reviewer-created" : levelLabel(mapping.level);
    const confidence = mapping.confidence == null ? "" : `<span class="mapping-confidence">${percent(mapping.confidence)} confidence</span>`;
    const lowConfidence = mapping.confidence_tier === "low";
    const lowTag = lowConfidence ? `<span class="low-confidence-tag"><i data-lucide="triangle-alert" aria-hidden="true"></i>Low confidence</span>` : "";
    const rationale = mapping.rationale ? `<p class="mapping-item-rationale">${escapeHtml(mapping.rationale)}</p>` : "";
    return `<div class="mapping-item ${proposed ? "is-proposed" : ""} ${lowConfidence ? "is-low-confidence" : ""}"><div class="mapping-item-heading"><span class="mapping-label">${escapeHtml(mapping.label)}</span><span class="mapping-confidence-group">${lowTag}${confidence}</span></div><span class="mapping-meta">${escapeHtml(sourceLabel)} · ${escapeHtml(mapping.display_id || mapping.id)}</span>${rationale}</div>`;
  }).join("");
}

function classificationChoices(selected) {
  return `<fieldset class="choice-field field-full"><legend class="field-label">Classification</legend><div class="classification-choices">${["Activity", "Output", "Outcome", "Indicator"].map((value) => `
    <label class="option-choice">
      <input class="option-input option-radio" type="radio" name="classification" value="${value}" ${value === selected ? "checked" : ""} />
      <span class="option-mark" aria-hidden="true"></span><span>${value}</span>
    </label>`).join("")}</div></fieldset>`;
}

function directionChoices(selected, visible) {
  return `<fieldset class="choice-field direction-choice-field" data-direction-field ${visible ? "" : "hidden"}><legend class="field-label">Outcome direction</legend><div class="direction-toggle">
    ${["Positive", "Negative"].map((value) => `<label><input type="radio" name="result_direction" value="${value}" ${value === selected ? "checked" : ""} ${visible ? "required" : "disabled"} /><span>${value}</span></label>`).join("")}
  </div></fieldset>`;
}

function mappingChoices(elements, selectedIds, label, proposedMappings = []) {
  const selected = new Set(selectedIds);
  const proposedById = new Map(proposedMappings.map((mapping) => [mapping.id, mapping]));
  return `<fieldset class="choice-field field-full mapping-choice-field"><legend class="field-label">${escapeHtml(label)}</legend><div class="mapping-choices">${elements.map((element) => `
    <label class="option-choice mapping-choice">
      <input class="option-input option-checkbox" type="checkbox" name="mapping_ids" value="${escapeHtml(element.id)}" ${selected.has(element.id) ? "checked" : ""} />
      <span class="option-mark" aria-hidden="true"></span>
      <span class="mapping-choice-copy"><strong>${escapeHtml(element.label)}</strong><small>${escapeHtml(element.display_id || element.id)}${proposedById.get(element.id)?.confidence_tier === "low" ? ` · Low confidence (${percent(proposedById.get(element.id).confidence)})` : ""}</small></span>
    </label>`).join("")}</div><p class="field-help">Select every applicable mapping, or leave all unchecked to record no suitable current match.</p></fieldset>`;
}

function languageFeedbackChoices() {
  const choices = [
    ["inaccurate_summary", "Inaccurate summary"],
    ["unsupported_or_invented_detail", "Unsupported or invented detail"],
    ["overstated_causal_claim", "Overstated causal claim"],
    ["inappropriate_terminology_or_tone", "Inappropriate terminology or tone"],
    ["unclear_or_unnecessarily_technical_language", "Unclear or unnecessarily technical language"],
    ["other_language_issue", "Other language issue"],
  ];
  return `<fieldset class="choice-field field-full language-feedback-field"><legend class="field-label">Provide feedback on the language MELVan used <span class="optional-label">Optional</span></legend><div class="language-feedback-choices">${choices.map(([value, label]) => `
    <label class="option-choice">
      <input class="option-input option-checkbox" type="checkbox" name="language_issue_types" value="${value}" />
      <span class="option-mark" aria-hidden="true"></span><span>${label}</span>
    </label>`).join("")}</div></fieldset>`;
}

function feedbackPanel(statement) {
  const analysis = statement.analysis;
  const noSuitableMatch = analysis.match_status === "no_suitable_match";
  if (noSuitableMatch) {
    return `
      <form class="feedback-panel" data-feedback-form data-no-suitable-match hidden>
        <div class="feedback-heading"><h2>Change MELvan's classification</h2><button class="icon-button" type="button" data-action="close-feedback" aria-label="Close changes" title="Close"><i data-lucide="x" aria-hidden="true"></i></button></div>
        <div class="feedback-grid">${classificationChoices(analysis.classification)}</div>
        <div class="feedback-footer"><button class="button button-primary" type="submit"><i data-lucide="save" aria-hidden="true"></i><span>Apply classification change</span></button></div>
      </form>`;
  }
  const selectedMappings = statement.mappings
    .filter((mapping) => mapping.status !== "provisional")
    .map((mapping) => mapping.id);
  const reject = statement.proposed_element ? `<label class="check-row field-full"><input type="checkbox" name="reject_proposal" /><span>Reject proposed element: ${escapeHtml(statement.proposed_element.label)}</span></label>` : "";
  return `
    <form class="feedback-panel" data-feedback-form hidden>
      <div class="feedback-heading"><h2>Change MELvan's analysis</h2><button class="icon-button" type="button" data-action="close-feedback" aria-label="Close changes" title="Close"><i data-lucide="x" aria-hidden="true"></i></button></div>
      <div class="feedback-grid">
        ${classificationChoices(analysis.classification)}
        ${directionChoices(analysis.result_direction, analysis.classification === "Outcome")}
        ${mappingChoices(state.elements, selectedMappings, "Existing mappings", statement.mappings)}
        ${reject}
        ${languageFeedbackChoices()}
        <div class="field field-full"><label class="field-label" for="comments-${escapeHtml(statement.id)}">Comments</label><textarea id="comments-${escapeHtml(statement.id)}" name="comments" rows="4" placeholder="Add any comments, corrections, or preferred wording for MELvan's language"></textarea></div>
      </div>
      <div class="feedback-footer"><button class="button button-primary" type="submit"><i data-lucide="badge-check" aria-hidden="true"></i><span>Apply changes and accept as Evidence</span></button></div>
    </form>`;
}

function renderEvidenceReview() {
  const root = $("#evidence-review-list");
  if (!state.evidenceReview.length) {
    root.innerHTML = emptyState("git-compare", "There is no evidence awaiting review for the current Logic Model.");
    return;
  }
  root.innerHTML = state.evidenceReview.map((item) => evidenceReviewCard(item)).join("");
}

function evidenceReviewCard(item) {
  const previous = item.accepted_mappings.length
    ? item.accepted_mappings.map(mappingLine).join("")
    : `<p class="muted">No approved mapping</p>`;
  const suggested = item.suggested_mappings.length
    ? item.suggested_mappings.map(mappingLine).join("")
    : `<p class="muted">No mapping suggested</p>`;
  const changes = item.change_set.length
    ? item.change_set.map(changeLine).join("")
    : `<p class="muted">No material differences were recorded.</p>`;
  const actions = state.workspaceReadOnly ? "" : `
      <div class="record-actions">
        <button class="button" type="button" data-evidence-action="edit"><i data-lucide="pencil" aria-hidden="true"></i><span>Change</span></button>
        <button class="button button-danger-subtle" type="button" data-evidence-action="not_relevant"><i data-lucide="circle-slash" aria-hidden="true"></i><span>Not relevant</span></button>
        <button class="button button-primary" type="button" data-evidence-action="accept"><i data-lucide="check" aria-hidden="true"></i><span>Accept</span></button>
      </div>
      <form class="feedback-panel evidence-not-relevant-form" hidden>
        <div class="feedback-heading"><h2>Confirm Not relevant</h2><button class="icon-button" type="button" data-evidence-action="cancel-not-relevant" aria-label="Cancel Not relevant" title="Cancel Not relevant"><i data-lucide="x" aria-hidden="true"></i></button></div>
        <p>This Evidence will be hidden under the current Logic Model. Its earlier acceptance and mappings will be preserved.</p>
        <div class="field"><label class="field-label">Reason (optional)</label><textarea name="feedback" rows="3" placeholder="Why is this Evidence no longer relevant?"></textarea></div>
        <div class="feedback-footer"><button class="button button-danger" type="submit"><i data-lucide="circle-slash" aria-hidden="true"></i><span>Confirm Not relevant</span></button></div>
      </form>
      <form class="feedback-panel evidence-assessment-form" hidden>
        <div class="feedback-heading"><h2>Change MELvan's reassessment</h2><button class="icon-button" type="button" data-evidence-action="close" aria-label="Close changes" title="Close changes"><i data-lucide="x" aria-hidden="true"></i></button></div>
        <div class="feedback-grid">
          ${classificationChoices(item.suggested_classification)}
          ${directionChoices(item.suggested_result_direction, item.suggested_classification === "Outcome")}
          ${mappingChoices(item.available_mappings, item.suggested_mappings.map((mapping) => mapping.id), `Mappings for ${item.target_logic_model.version}`, item.suggested_mappings)}
          ${languageFeedbackChoices()}
          <div class="field field-full"><label class="field-label">Comments</label><textarea name="feedback" rows="4" placeholder="Add any comments, corrections, or preferred wording for MELvan's language"></textarea></div>
        </div>
        <div class="feedback-footer"><button class="button button-primary" type="submit" value="change"><i data-lucide="save" aria-hidden="true"></i><span>Apply changes</span></button><button class="button" type="submit" value="revise"><i data-lucide="refresh-cw" aria-hidden="true"></i><span>Request revised suggestion</span></button></div>
      </form>`;
  return `
    <article class="record-card evidence-review-card" data-assessment-id="${escapeHtml(item.id)}" data-suggestion-identity="${escapeHtml(item.suggestion_identity)}">
      <div class="record-main">
        <div class="record-topline"><div><p class="eyebrow">Evidence reassessment</p><p class="statement-text">${escapeHtml(item.original_text)}</p></div><span class="record-meta">Logic Model ${escapeHtml(item.target_logic_model.version)}</span></div>
        <p class="analysis-summary">${escapeHtml(item.summary)}</p>
        <section class="what-changed"><div class="what-changed-heading"><i data-lucide="git-compare-arrows" aria-hidden="true"></i><h3>What changed</h3></div><div class="change-list">${changes}</div></section>
        <div class="comparison-grid">
          <section><h3>Accepted under ${escapeHtml(item.accepted_logic_model?.version || "earlier version")}</h3><div class="comparison-tags"><span class="tag">${escapeHtml(item.accepted_classification)}</span>${directionTag(item.accepted_result_direction)}</div><div class="mapping-list">${previous}</div></section>
          <section><h3>Proposed for ${escapeHtml(item.target_logic_model.version)}</h3><div class="comparison-tags"><span class="tag">${escapeHtml(item.suggested_classification)}</span>${directionTag(item.suggested_result_direction)}</div><div class="mapping-list">${suggested}</div>${!item.suggested_mappings.length && item.mapping_rationale ? `<p class="mapping-rationale">${escapeHtml(item.mapping_rationale)}</p>` : ""}</section>
        </div>
      </div>
      ${actions}
    </article>`;
}

function directionTag(direction) {
  return direction
    ? `<span class="tag ${direction === "Negative" ? "tag-negative" : "tag-positive"}">${escapeHtml(direction)}</span>`
    : "";
}

function changeLine(change) {
  const labels = {
    classification_changed: "Classification changed",
    mapping_added: "Mapping added",
    mapping_removed: "Mapping removed",
    mapping_replaced: "Mapping replaced",
    mapping_confidence_changed: "Mapping confidence changed",
    no_suitable_match: "No suitable current match",
  };
  const confidence = change.confidence_delta_percentage_points == null
    ? ""
    : `<span>${escapeHtml(change.confidence_delta_percentage_points)} percentage points</span>`;
  return `<div class="change-item"><div><strong>${escapeHtml(labels[change.type] || titleCase(change.type))}</strong>${confidence}</div><p>${escapeHtml(change.explanation)}</p></div>`;
}

function mappingLine(mapping) {
  const confidence = mapping.confidence == null
    ? `<span class="mapping-confidence">Confidence unavailable</span>`
    : `<span class="mapping-confidence">${percent(mapping.confidence)} confidence</span>`;
  const rationale = mapping.rationale ? `<p class="mapping-item-rationale">${escapeHtml(mapping.rationale)}</p>` : "";
  const lowConfidence = mapping.confidence_tier === "low";
  const lowTag = lowConfidence ? `<span class="low-confidence-tag"><i data-lucide="triangle-alert" aria-hidden="true"></i>Low confidence</span>` : "";
  return `<div class="mapping-item ${lowConfidence ? "is-low-confidence" : ""}"><div class="mapping-item-heading"><span class="mapping-label">${escapeHtml(mapping.label)}</span><span class="mapping-confidence-group">${lowTag}${confidence}</span></div><span class="mapping-meta">${escapeHtml(mapping.display_id || mapping.id)}</span>${rationale}</div>`;
}

function renderEvidence() {
  const root = $("#evidence-list");
  if (!state.evidence.length) {
    root.innerHTML = emptyState("archive", "No evidence has been approved yet.");
    return;
  }
  root.innerHTML = state.evidence.map((statement) => `<article class="record-card"><div class="record-main"><div class="record-topline"><div><p class="eyebrow">Evidence</p><p class="statement-text">${escapeHtml(statement.original_text)}</p></div><span class="record-meta">Approved ${escapeHtml(formatDate(statement.evidence.accepted_at))}</span></div>${captureProvenanceView(statement)}${contextView(statement)}${statementPhotoView(statement)}${analysisView(statement)}</div></article>`).join("");
  loadStatementPhotos();
}

function renderQuality() {
  const measure = $("#quality-measure");
  if (!state.quality.total) {
    measure.hidden = true;
    return;
  }
  measure.hidden = false;
  $("#quality-percentage").textContent = `${state.quality.percentage}%`;
  $("#quality-fraction").textContent = `${state.quality.matches} of ${state.quality.total}`;
}

function renderLogicModel() {
  const root = $("#logic-model-view");
  root.hidden = state.logicEditing;
  $("#logic-model-admin-action").hidden = !canAdminister() || state.logicEditing || !state.logicModel?.is_current;
  const model = state.logicModel;
  if (!model) {
    root.innerHTML = `<div class="loading-state">Logic Model is unavailable.</div>`;
    return;
  }
  if (model.status === "empty") {
    $("#logic-model-admin-action").hidden = true;
    root.innerHTML = canAdminister()
      ? `<section class="empty-workspace"><i data-lucide="network" aria-hidden="true"></i><h1>Create this workspace's Logic Model</h1><p>Start with an empty Logic Model or pre-load the MELvan demonstration model.</p><div><button class="button button-primary" type="button" data-empty-logic-action="create"><i data-lucide="plus" aria-hidden="true"></i><span>Create Logic Model</span></button><button class="button" type="button" data-empty-logic-action="preload"><i data-lucide="copy" aria-hidden="true"></i><span>Pre-load demo Logic Model</span></button></div></section>`
      : emptyState("book-open", "No Logic Model has been created for this workspace.");
    refreshIcons();
    return;
  }
  const recap = `<section class="logic-level recap-view"><h2>${recapReferenceLink()}</h2><div class="recap-grid">${Object.entries(RECAP_FIELDS).map(([key, field]) => {
    const content = model.recap?.[key]?.text || "";
    return `<div class="recap-group"><div class="recap-heading"><h3>${field.label}</h3><p class="recap-prompt">${field.prompt}</p></div>${content ? `<p class="recap-content">${escapeHtml(content)}</p>` : `<p class="muted">No content</p>`}</div>`;
  }).join("")}</div></section>`;
  const levelOrder = ["ultimate_goal", "long_term_outcome", "medium_term_outcome", "short_term_outcome", "output"];
  const levelTitles = { output: "Outputs", short_term_outcome: "Short-term outcomes", medium_term_outcome: "Medium-term outcomes", long_term_outcome: "Long-term outcomes", ultimate_goal: "Ultimate Goal" };
  const sections = levelOrder.map((level) => {
    const elements = model.elements.filter((element) => element.level === level);
    if (!elements.length) return "";
    if (level !== "output") {
      return `<section class="logic-level"><h2>${escapeHtml(levelTitles[level])}</h2><div class="logic-elements">${elements.map((element) => `<div class="logic-element"><span class="logic-element-label">${escapeHtml(element.label)}</span><span class="logic-element-id">${escapeHtml(element.id)}</span></div>`).join("")}</div></section>`;
    }
    const groups = new Map();
    elements.forEach((element) => {
      const group = element.group || "Other/uncategorized";
      if (!groups.has(group)) groups.set(group, []);
      groups.get(group).push(element);
    });
    return `<section class="logic-level"><h2>${escapeHtml(levelTitles[level])}</h2><div class="logic-groups">${[...groups.entries()].map(([group, items]) => `<div class="logic-group"><h3>${escapeHtml(group)}</h3><div class="logic-elements">${items.map((element) => `<div class="logic-element"><span class="logic-element-label">${escapeHtml(element.label)}</span><span class="logic-element-id">${escapeHtml(element.id)}</span></div>`).join("")}</div></div>`).join("")}</div></section>`;
  }).join("");
  const activation = canAdminister() && model.activation
    ? activationStatus(model.activation)
    : "";
  root.innerHTML = `<div class="logic-model-summary"><div><p class="eyebrow">${model.is_current ? "Current Logic Model" : "Version preview"}</p><h2>${escapeHtml(model.title)}</h2></div><dl><div><dt>Version</dt><dd>${escapeHtml(model.version)}</dd></div><div><dt>Elements</dt><dd>${model.elements.length}</dd></div></dl></div>${activation}<div class="logic-model-content">${sections}${recap}</div>`;
}

function recapReferenceLink() {
  return `<a class="recap-reference-link" href="${RECAP_REFERENCE_URL}" target="_blank" rel="noopener noreferrer">RECAP</a>`;
}

async function handleEmptyLogicModelAction(event) {
  const button = event.target.closest("[data-empty-logic-action]");
  if (!button || !canAdminister()) return;
  if (button.dataset.emptyLogicAction === "create") {
    await startLogicModelEdit();
    return;
  }
  if (!window.confirm("Pre-load a separate copy of the MELvan demonstration Logic Model for this workspace?")) return;
  button.disabled = true;
  try {
    await api("/api/admin/logic-model/preload-demo", {
      method: "POST",
      body: { confirmed: true },
    });
    showToast("Demo Logic Model pre-loaded.");
    await refreshData();
  } catch (error) {
    showToast(error.message, true);
  } finally {
    button.disabled = false;
  }
}

function activationStatus(activation) {
  const records = activation.manifest?.records || [];
  const queued = records.filter((item) => item.record_type === "evidence" && item.outcome === "new").length;
  const reused = records.filter((item) => item.record_type === "evidence" && item.outcome === "reused").length;
  const states = {
    failed: { label: "Logic Model activation failed", icon: "circle-alert" },
    preparing: { label: "Preparing Logic Model activation", icon: "loader-circle" },
    no_op: { label: "No changes required", icon: "circle-check" },
    completed: { label: "Logic Model activation complete", icon: "circle-check" },
  };
  const status = states[activation.status] || states.completed;
  const detail = activation.status === "failed"
    ? activation.error_message || "The previous Logic Model remains current."
    : `${queued} Evidence ${queued === 1 ? "record" : "records"} queued for review · ${reused} reused`;
  return `<div class="activation-status is-${escapeHtml(activation.status)}"><i data-lucide="${status.icon}" aria-hidden="true"></i><div><strong>${status.label}</strong><span>${escapeHtml(detail)}</span></div></div>`;
}

function renderLogicModelEditor() {
  const root = $("#logic-model-editor");
  root.hidden = !state.logicEditing;
  if (!state.logicEditing || !state.logicModelDraft) {
    root.innerHTML = "";
    return;
  }
  const draft = state.logicModelDraft;
  const levelLabels = { ultimate_goal: "Ultimate Goal", long_term_outcome: "Long-term outcomes", medium_term_outcome: "Medium-term outcomes", short_term_outcome: "Short-term outcomes" };
  const levelCaps = { short_term_outcome: 9, medium_term_outcome: 7, long_term_outcome: 5, ultimate_goal: 1 };
  const recap = Object.entries(RECAP_FIELDS).map(([key, field]) => `<section class="builder-section recap-editor"><div class="builder-section-heading"><div class="recap-heading"><h2>${field.label}</h2><p class="recap-prompt">${field.prompt}</p></div><p class="field-note">use numbering if you want to present ${field.label.toLowerCase()} as a list e.g. 1. This is ${key === "people" ? "person" : key.replace(/s$/, "")} one. 2. This is ${key === "people" ? "person" : key.replace(/s$/, "")} two.</p></div><textarea rows="6" data-recap-key="${key}" aria-label="${field.label}">${escapeHtml(draft.recap[key]?.text || "")}</textarea></section>`).join("");
  const outputs = draft.elements.filter((item) => item.type === "Output");
  const outputRows = outputs.map((item) => logicElementEditor(item, draft.elements.indexOf(item))).join("");
  const outcomes = Object.entries(levelLabels).map(([level, label]) => {
    const items = draft.elements.filter((item) => item.type === "Outcome" && item.level === level);
    const add = level === "ultimate_goal" || items.length >= levelCaps[level] ? "" : `<span class="outcome-add"><span>Up to ${levelCaps[level]}</span><button class="icon-button" type="button" data-logic-action="add-outcome" data-outcome-level="${level}" aria-label="Add ${label.toLowerCase().replace(/s$/, "")}" title="Add ${label.toLowerCase().replace(/s$/, "")}"><i data-lucide="plus" aria-hidden="true"></i></button></span>`;
    return `<section class="builder-section"><div class="builder-section-heading"><h2>${label}</h2>${add}</div><div class="builder-list">${items.map((item) => logicElementEditor(item, draft.elements.indexOf(item))).join("") || `<p class="muted">No ${label.toLowerCase()} added</p>`}</div></section>`;
  }).join("");
  root.innerHTML = `<div class="logic-editor-header"><div class="field"><label class="field-label" for="logic-title">Logic Model name</label><input id="logic-title" type="text" data-logic-title value="${escapeHtml(draft.title)}" /></div><div class="autosave-state" id="logic-autosave-state">Changes autosave as a draft</div></div><section class="builder-band"><div class="builder-band-heading"><p class="eyebrow">Logic Model elements</p><h1>Outcomes</h1></div>${outcomes}</section><section class="builder-band"><div class="builder-band-heading"><p class="eyebrow">Logic Model elements</p><h1>Outputs</h1><button class="button" type="button" data-logic-action="add-output"><i data-lucide="plus" aria-hidden="true"></i><span>Add an output</span></button></div><div class="builder-list">${outputRows || `<p class="muted">Add at least one Output before committing.</p>`}</div></section><section class="builder-band"><div class="builder-band-heading"><p class="eyebrow">Optional context</p><h1>${recapReferenceLink()}</h1></div>${recap}</section><div class="logic-editor-actions"><button class="button button-danger-text" type="button" data-logic-action="cancel-all"><i data-lucide="circle-x" aria-hidden="true"></i><span>Cancel all changes</span></button><div><button class="button" type="button" data-logic-action="save-later"><i data-lucide="save" aria-hidden="true"></i><span>Save changes for later</span></button><button class="button button-primary" type="button" data-logic-action="commit"><i data-lucide="git-commit-horizontal" aria-hidden="true"></i><span>Commit updates</span></button></div></div>`;
  refreshIcons();
}

function logicElementEditor(item, index) {
  const kind = item.level === "ultimate_goal" ? "Ultimate Goal" : item.type;
  const category = item.type === "Output" ? `<div class="field"><label class="field-label">Category (optional)</label><input type="text" data-element-field="group" value="${escapeHtml(item.group || "")}" /></div>` : "";
  return `<div class="builder-element ${item.type === "Outcome" ? "without-category" : ""}" data-element-index="${index}"><span class="logic-element-id">${escapeHtml(item.display_id || item.id)}</span>${category}<div class="field builder-text"><label class="field-label">${kind}</label><textarea rows="2" data-element-field="label" placeholder="Enter your ${item.type.toLowerCase()} here">${escapeHtml(item.label)}</textarea></div>${item.level === "ultimate_goal" ? "" : `<button class="icon-button" type="button" data-logic-action="delete-element" data-element-index="${index}" aria-label="Delete ${kind}" title="Delete"><i data-lucide="trash-2" aria-hidden="true"></i></button>`}</div>`;
}

function renderLogicVersions() {
  const toolbar = $("#logic-version-toolbar");
  const canSelectVersion = canAdminister() && !state.logicEditing && state.logicModelVersions.length > 1;
  toolbar.hidden = !canSelectVersion;
  if (!canSelectVersion) return;
  const select = $("#logic-version-select");
  select.innerHTML = state.logicModelVersions.map((version) => `<option value="${escapeHtml(version.id)}" ${state.logicEditing ? (version.is_draft ? "selected" : "") : (version.id === state.logicModel.database_id ? "selected" : "")}>${version.is_draft ? "In progress Logic Model" : `Version ${escapeHtml(version.version)}${version.is_current ? " · Current" : ""}`}</option>`).join("");
  $("#use-logic-version").hidden = state.logicEditing || Boolean(state.logicModel?.is_current);
}

async function previewLogicModel(event) {
  if (event.target.value === "draft") {
    await startLogicModelEdit();
    return;
  }
  try {
    const payload = await api(`/api/logic-model/${encodeURIComponent(event.target.value)}`);
    state.logicModel = payload.logic_model;
    renderLogicModel();
    $("#use-logic-version").hidden = state.logicModel.is_current;
    refreshIcons();
  } catch (error) {
    showToast(error.message, true);
  }
}

async function startLogicModelEdit() {
  if (!canAdminister()) return;
  try {
    const payload = await api("/api/admin/logic-model-draft/start", { method: "POST", body: {} });
    state.logicModelDraft = payload.draft;
    if (!state.logicModelVersions.some((version) => version.is_draft)) {
      state.logicModelVersions.unshift({
        id: "draft",
        title: payload.draft.title || "Untitled Logic Model",
        version: "In progress",
        is_current: false,
        is_draft: true,
      });
    }
    state.logicEditing = true;
    configureAccess();
    renderLogicModel();
    renderLogicModelEditor();
    renderLogicVersions();
    $("#logic-title")?.focus();
  } catch (error) {
    showToast(error.message, true);
  }
}

function handleLogicModelInput(event) {
  if (!state.logicModelDraft) return;
  const target = event.target;
  if (target.matches("[data-logic-title]")) {
    state.logicModelDraft.title = target.value;
  } else if (target.dataset.elementField) {
    const index = Number(target.closest("[data-element-index]").dataset.elementIndex);
    state.logicModelDraft.elements[index][target.dataset.elementField] = target.value;
  } else if (target.dataset.recapKey) {
    state.logicModelDraft.recap[target.dataset.recapKey].text = target.value;
  } else {
    return;
  }
  scheduleLogicModelAutosave();
}

function scheduleLogicModelAutosave() {
  clearTimeout(logicAutosaveTimer);
  const status = $("#logic-autosave-state");
  if (status) status.textContent = "Saving draft...";
  logicAutosaveTimer = setTimeout(() => saveLogicModelDraft(), 500);
}

async function saveLogicModelDraft(confirmedDeletions = []) {
  clearTimeout(logicAutosaveTimer);
  logicAutosaveTimer = null;
  if (!state.logicModelDraft) return;
  const status = $("#logic-autosave-state");
  try {
    const payload = await api("/api/admin/logic-model-draft/save", {
      method: "POST",
      body: { draft: state.logicModelDraft, confirmed_deletions: confirmedDeletions },
    });
    state.logicModelDraft = payload.draft;
    if (status) status.textContent = "Draft saved";
  } catch (error) {
    if (status) status.textContent = "Draft not saved";
    showToast(error.message, true);
    throw error;
  }
}

async function handleLogicModelClick(event) {
  const button = event.target.closest("[data-logic-action]");
  if (!button || !state.logicModelDraft) return;
  const action = button.dataset.logicAction;
  if (action === "add-output") {
    state.logicModelDraft.elements.unshift({ stable_id: localDraftId("output"), revision: 0, type: "Output", level: "output", group: "", label: "" });
    await saveLogicModelDraft();
    renderLogicModelEditor();
  } else if (action === "add-outcome") {
    const level = button.dataset.outcomeLevel;
    const caps = { short_term_outcome: 9, medium_term_outcome: 7, long_term_outcome: 5, ultimate_goal: 1 };
    const count = state.logicModelDraft.elements.filter((item) => item.type === "Outcome" && item.level === level).length;
    if (count >= caps[level]) {
      showToast(`The ${titleCase(level.replaceAll("_", " "))} limit has been reached.`, true);
      return;
    }
    state.logicModelDraft.elements.push({ stable_id: localDraftId("outcome"), revision: 0, type: "Outcome", level, group: "", label: "" });
    await saveLogicModelDraft();
    renderLogicModelEditor();
  } else if (action === "delete-element") {
    await confirmLogicModelDeletion(button);
  } else if (action === "save-later") {
    await saveLogicModelDraft();
    state.logicEditing = false;
    showToast("Logic Model draft saved for later.");
    await refreshData();
  } else if (action === "cancel-all") {
    confirmCancelLogicModelDraft();
  } else if (action === "commit") {
    await openLogicModelCommit();
  }
}

function localDraftId(prefix) {
  return `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

async function confirmLogicModelDeletion(button) {
  const dialog = $("#confirm-logic-delete");
  dialog.showModal();
  dialog.addEventListener("close", async () => {
    if (dialog.returnValue !== "confirm") return;
    const removed = state.logicModelDraft.elements.splice(Number(button.dataset.elementIndex), 1)[0];
    await saveLogicModelDraft([removed.stable_id]);
    renderLogicModelEditor();
  }, { once: true });
}

function confirmCancelLogicModelDraft() {
  const dialog = $("#confirm-logic-cancel");
  dialog.showModal();
  dialog.addEventListener("close", async () => {
    if (dialog.returnValue !== "confirm") return;
    try {
      await api("/api/admin/logic-model-draft/cancel", { method: "POST", body: { confirmed: true } });
      state.logicModelDraft = null;
      state.logicEditing = false;
      showToast("All draft changes were cancelled.");
      await refreshData();
    } catch (error) {
      showToast(error.message, true);
    }
  }, { once: true });
}

async function openLogicModelCommit() {
  try {
    await saveLogicModelDraft();
    const payload = await api("/api/admin/logic-model-draft/commit-summary");
    const summary = payload.summary;
    $("#logic-commit-summary").innerHTML = `<dl class="commit-summary"><div><dt>RECAP changes</dt><dd>${summary.recap_changes}</dd></div><div><dt>Elements added</dt><dd>${summary.outputs_added + summary.outcomes_added}</dd></div><div><dt>Elements edited</dt><dd>${summary.elements_edited}</dd></div><div><dt>Elements deleted</dt><dd>${summary.elements_deleted}</dd></div></dl><p>${escapeHtml(summary.impact.rationale)}</p>`;
    const dialog = $("#confirm-logic-commit");
    dialog.showModal();
    refreshIcons();
    dialog.addEventListener("close", async () => {
      if (dialog.returnValue !== "confirm") return;
      try {
        await api("/api/admin/logic-model-draft/commit", { method: "POST", body: { confirmed: true } });
        state.logicModelDraft = null;
        state.logicEditing = false;
        showToast("Logic Model updates committed.");
        await refreshData();
      } catch (error) {
        showToast(error.message, true);
      }
    }, { once: true });
  } catch (error) {
    showToast(error.message, true);
  }
}

async function confirmLogicModelActivation() {
  if (!state.logicModel || state.logicModel.is_current) return;
  const dialog = $("#confirm-logic-version");
  const hasDraft = Boolean(state.logicModelDraft);
  $("#confirm-logic-copy").textContent = hasDraft
    ? "Any changes or additions to other Logic Models will be lost. To save them, you should cancel this rollback, update the Logic Model from draft to current, then roll back to this version."
    : `Version ${state.logicModel.version} will become current. Only classifications or mappings affected by element changes will require review.`;
  $("button[value='confirm']", dialog).textContent = hasDraft ? "Continue and lose changes" : "Use this version";
  dialog.showModal();
  dialog.addEventListener("close", async () => {
    if (dialog.returnValue !== "confirm") return;
    try {
      await api(`/api/admin/logic-models/${encodeURIComponent(state.logicModel.database_id)}/activate`, { method: "POST", body: { confirmed: true, discard_draft: hasDraft } });
      showToast(`Logic Model ${state.logicModel.version} is now current.`);
      await refreshData();
    } catch (error) {
      showToast(error.message, true);
    }
  }, { once: true });
}

function renderUsers() {
  if (!canViewUsers()) return;
  const root = $("#users-list");
  const ownerHome = isOwnerHome();
  root.innerHTML = `<div class="users-row users-header"><span>${ownerHome ? "Administrator" : "User"}</span><span>${ownerHome ? "Workspace nickname" : "Roles"}</span><span>Last login</span><span>Logins</span><span>Statements</span><span>Reviewed</span><span>Status</span><span class="sr-only">Actions</span></div>${state.users.map((user) => {
    const statementCount = user.roles.includes("reporter") ? user.statement_count : "—";
    const reviewedCount = user.roles.includes("reviewer") ? user.reviewed_statement_count : "—";
    const isAdmin = user.roles.includes("administrator");
    const locked = user.authentication_status === "temporary_lockout";
    const inherited = Boolean(user.inherited_suspension);
    const statusText = inherited ? "Workspace suspended" : titleCase(user.status);
    const accessStatus = `<span class="status-stack"><span class="status-label ${user.effective_status === "suspended" ? "is-suspended" : ""}">${escapeHtml(statusText)}</span>${locked ? `<span class="status-label is-locked">Temporary lockout</span><small>Until ${escapeHtml(formatDate(user.locked_until))}</small>` : ""}</span>`;
    const canSuspend = canAdminister() && !user.roles.includes("owner") && (roles().has("owner") || !isAdmin);
    const workspaceLabel = !ownerHome && isAdmin && !user.roles.includes("owner")
      ? `<small>Workspace: ${escapeHtml(user.workspace_nickname)}</small>`
      : "";
    const actions = canAdminister()
      ? `<span class="row-actions"><button class="icon-button" type="button" data-user-action="edit" aria-label="Edit ${escapeHtml(user.first_name)}" title="Edit user"><i data-lucide="pencil" aria-hidden="true"></i></button>${canSuspend ? `<button class="icon-button" type="button" data-user-action="${user.status === "active" ? "suspend" : "reactivate"}" aria-label="${user.status === "active" ? "Suspend" : "Reactivate"} ${escapeHtml(user.first_name)}" title="${user.status === "active" ? "Suspend" : "Reactivate"} user"><i data-lucide="${user.status === "active" ? "user-x" : "user-check"}" aria-hidden="true"></i></button>` : ""}</span>`
      : `<span class="row-actions"></span>`;
    const roleOrWorkspace = ownerHome
      ? user.workspace_nickname
      : user.roles.map(titleCase).join(", ");
    return `<div class="users-row" data-user-id="${escapeHtml(user.id)}"><span><strong>${escapeHtml(user.first_name)} ${escapeHtml(user.last_name)}</strong><small>${escapeHtml(user.email)}</small>${workspaceLabel}</span><span>${escapeHtml(roleOrWorkspace)}</span><span>${user.last_login_at ? escapeHtml(formatDate(user.last_login_at)) : "Never"}</span><span>${escapeHtml(user.total_logins)}</span><span>${escapeHtml(statementCount)}</span><span>${escapeHtml(reviewedCount)}</span>${accessStatus}${actions}</div>`;
  }).join("")}`;
  refreshIcons();
}

function showUserForm(user = null) {
  if (!canAdminister()) return;
  const root = $("#user-form-region");
  const ownerAdministratorForm = isOwnerHome();
  const isOwnerUser = user?.roles.includes("owner");
  const isAdministrator = Boolean(user?.roles.includes("administrator"));
  const ownerCanSetNickname = ownerAdministratorForm && !isOwnerUser;
  const administratorOption = user?.roles.includes("administrator")
    ? `<label class="check-row"><input type="checkbox" checked disabled /><span>Administrator</span></label>`
    : roles().has("owner")
      ? `<label class="check-row"><input type="checkbox" name="roles" value="administrator" /><span>Administrator</span></label>`
      : "";
  const nicknameField = ownerCanSetNickname
    ? `<div class="field workspace-nickname-field" data-workspace-nickname-field><label class="field-label">Workspace nickname</label><input name="workspace_nickname" aria-label="Workspace nickname" type="text" maxlength="40" value="${escapeHtml(user?.workspace_nickname || "")}" required /></div>`
    : "";
  const rolesField = ownerAdministratorForm
    ? ""
    : `<fieldset class="role-options"><legend>Roles</legend>${isOwnerUser ? `<label class="check-row"><input type="checkbox" checked disabled /><span>Owner</span></label>` : ""}${administratorOption}<label class="check-row"><input type="checkbox" name="roles" value="reporter" ${user?.roles.includes("reporter") ? "checked" : ""} /><span>Reporter</span></label><label class="check-row"><input type="checkbox" name="roles" value="reviewer" ${user?.roles.includes("reviewer") ? "checked" : ""} /><span>Reviewer</span></label></fieldset>`;
  const formTitle = ownerAdministratorForm
    ? user ? "Edit administrator" : "Create a new administrator"
    : user ? "Edit user" : "New user";
  const submitLabel = ownerAdministratorForm
    ? user ? "Save administrator" : "Create administrator"
    : "Save user";
  root.innerHTML = `<form class="user-form" data-user-id="${escapeHtml(user?.id || "")}" data-owner-administrator="${ownerAdministratorForm}"><div class="feedback-heading"><h3>${formTitle}</h3><button class="icon-button" type="button" data-cancel-user aria-label="Close user form" title="Close"><i data-lucide="x" aria-hidden="true"></i></button></div><div class="user-form-grid"><div class="field"><label class="field-label">First name</label><input name="first_name" aria-label="First name" type="text" value="${escapeHtml(user?.first_name || "")}" required /></div><div class="field"><label class="field-label">Last name</label><input name="last_name" aria-label="Last name" type="text" value="${escapeHtml(user?.last_name || "")}" required /></div><div class="field email-field"><label class="field-label">Email address</label><input name="email" aria-label="Email address" type="email" maxlength="254" value="${escapeHtml(user?.email || "")}" required /></div>${nicknameField}${rolesField}</div><div class="feedback-footer"><button class="button button-primary" type="submit"><i data-lucide="save" aria-hidden="true"></i><span>${submitLabel}</span></button></div></form>`;
  root.hidden = false;
  refreshIcons();
}

function handleUserFormChange(event) {
  if (!event.target.matches('input[name="roles"][value="administrator"]')) return;
  const form = event.target.closest(".user-form");
  const field = $("[data-workspace-nickname-field]", form);
  if (!field) return;
  const input = $('input[name="workspace_nickname"]', field);
  field.hidden = !event.target.checked;
  input.disabled = !event.target.checked;
  input.required = event.target.checked;
  if (event.target.checked) input.focus();
}

async function submitUserForm(event) {
  if (!event.target.matches(".user-form")) return;
  event.preventDefault();
  const form = event.target;
  const userId = form.dataset.userId;
  const body = {
    first_name: form.elements.first_name.value,
    last_name: form.elements.last_name.value,
    email: form.elements.email.value,
    roles: form.dataset.ownerAdministrator === "true"
      ? ["administrator"]
      : $$('input[name="roles"]:checked', form).map((input) => input.value),
  };
  if (form.elements.workspace_nickname && !form.elements.workspace_nickname.disabled) {
    body.workspace_nickname = form.elements.workspace_nickname.value;
  }
  try {
    await api(userId ? `/api/admin/users/${encodeURIComponent(userId)}` : "/api/admin/users", { method: "POST", body });
    $("#user-form-region").hidden = true;
    showToast(userId ? "User updated." : "User created.");
    await refreshData();
  } catch (error) {
    showToast(error.message, true);
  }
}

async function exportEvidencePDF() {
  const buttons = [$("#export-evidence"), $("#reviewer-export-evidence")];
  buttons.forEach((button) => { button.disabled = true; });
  try {
    const payload = await api("/api/evidence-export", { method: "POST", body: {} });
    const binary = atob(payload.data_base64);
    const bytes = new Uint8Array(binary.length);
    for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
    const url = URL.createObjectURL(new Blob([bytes], { type: payload.content_type }));
    const link = document.createElement("a");
    link.href = url;
    link.download = payload.filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    showToast(`${payload.record_count} Evidence record${payload.record_count === 1 ? "" : "s"} exported.`);
  } catch (error) {
    showToast(error.message, true);
  } finally {
    buttons.forEach((button) => { button.disabled = false; });
  }
}

async function handleUserClick(event) {
  if (!canAdminister()) return;
  const button = event.target.closest("[data-user-action]");
  if (!button) return;
  const user = state.users.find((item) => item.id === button.closest("[data-user-id]").dataset.userId);
  if (!user) return;
  if (button.dataset.userAction === "edit") {
    showUserForm(user);
    return;
  }
  const isAdministrator = user.roles.includes("administrator") && !user.roles.includes("owner");
  if (isAdministrator && button.dataset.userAction === "suspend") {
    const confirmed = window.confirm(
      `Suspend ${user.first_name} ${user.last_name} and every account in ${user.workspace_nickname}?`
    );
    if (!confirmed) return;
  }
  try {
    const payload = await api(`/api/admin/users/${encodeURIComponent(user.id)}/${button.dataset.userAction}`, { method: "POST", body: {} });
    const affected = Number(payload.user.affected_account_count || 1);
    showToast(
      isAdministrator
        ? `${user.workspace_nickname} ${button.dataset.userAction === "suspend" ? "suspended" : "reactivated"} for ${affected} account${affected === 1 ? "" : "s"}.`
        : button.dataset.userAction === "suspend" ? "User suspended." : "User reactivated."
    );
    await refreshData();
  } catch (error) {
    showToast(error.message, true);
  }
}

async function handleReviewClick(event) {
  const button = event.target.closest("button[data-action]");
  if (!button) return;
  const card = button.closest(".record-card");
  const statement = state.review.find((item) => item.id === card?.dataset.statementId);
  if (!statement) return;
  if (button.dataset.action === "feedback") {
    const panel = $("[data-feedback-form]", card);
    panel.hidden = false;
    button.setAttribute("aria-expanded", "true");
    $("input[name='classification']:checked", panel)?.focus();
  } else if (button.dataset.action === "close-feedback") {
    $("[data-feedback-form]", card).hidden = true;
  } else if (button.dataset.action === "accept") {
    await acceptStatement(statement, button);
  } else if (button.dataset.action === "not-relevant") {
    await markNotRelevant(statement, button);
  }
  refreshIcons();
}

function checkedValue(form, name) {
  return $(`input[name='${name}']:checked`, form)?.value || "";
}

function syncDirectionField(form, classification) {
  const field = $("[data-direction-field]", form);
  if (!field) return;
  const isOutcome = classification === "Outcome";
  field.hidden = !isOutcome;
  $$(`input[name='result_direction']`, field).forEach((input) => {
    input.disabled = !isOutcome;
    input.required = isOutcome;
  });
}

function selectedMappingIds(form) {
  return $$("input[name='mapping_ids']:checked", form).map((input) => input.value);
}

function sameIds(left, right) {
  return [...left].sort().join("|") === [...right].sort().join("|");
}

function handleReviewChange(event) {
  const form = event.target.closest("form");
  if (!form) return;
  if (event.target.name === "classification") {
    syncDirectionField(form, event.target.value);
    return;
  }
}

async function handleFeedbackSubmit(event) {
  if (!event.target.matches("[data-feedback-form]")) return;
  event.preventDefault();
  const form = event.target;
  const statement = state.review.find((item) => item.id === form.closest(".record-card").dataset.statementId);
  const classification = checkedValue(form, "classification");
  const direction = checkedValue(form, "result_direction");
  const noSuitableMatch = form.hasAttribute("data-no-suitable-match");
  if (noSuitableMatch && classification === statement.analysis.classification) {
    showToast("Choose a different classification before applying the change.", true);
    return;
  }
  const body = {
    analysis_id: statement.analysis.id,
    corrected_classification: classification !== statement.analysis.classification
      ? classification : null,
    corrected_result_direction: classification === "Outcome"
      && direction !== (statement.analysis.result_direction || "")
      ? direction : null,
    comments: $("textarea[name='comments']", form)?.value.trim() || null,
    language_issue_types: $$('input[name="language_issue_types"]:checked', form).map((input) => input.value),
    reject_proposal: Boolean($("input[name='reject_proposal']", form)?.checked),
    accept_as_evidence: !noSuitableMatch,
  };
  if (body.language_issue_types.includes("other_language_issue") && !body.comments) {
    showToast("Describe the other language issue in the comments field.", true);
    return;
  }
  if (!noSuitableMatch) {
    const selected = selectedMappingIds(form);
    const current = statement.mappings.filter((mapping) => mapping.status !== "provisional").map((mapping) => mapping.id);
    if (!sameIds(selected, current)) body.mapping_ids = selected;
  }
  const submit = $("button[type='submit']", form);
  submit.disabled = true;
  try {
    await api(`/api/statements/${encodeURIComponent(statement.id)}/feedback`, { method: "POST", body });
    showToast(noSuitableMatch
      ? "Classification updated. The Statement remains awaiting review."
      : "Changes applied and Statement accepted as Evidence.");
    await refreshData();
  } catch (error) {
    showToast(error.message, true);
  } finally {
    submit.disabled = false;
  }
}

async function acceptStatement(statement, button) {
  button.disabled = true;
  try {
    await api(`/api/statements/${encodeURIComponent(statement.id)}/accept`, { method: "POST", body: { analysis_id: statement.analysis.id } });
    showToast("Statement approved and added to Evidence.");
    await refreshData();
  } catch (error) {
    showToast(error.message, true);
    button.disabled = false;
  }
}

async function markNotRelevant(statement, button) {
  if (!window.confirm("Mark this Statement as not relevant to the program? It will be removed from every user-facing area.")) return;
  button.disabled = true;
  try {
    await api(`/api/statements/${encodeURIComponent(statement.id)}/not-relevant`, {
      method: "POST",
      body: { analysis_id: statement.analysis.id },
    });
    showToast("Statement marked as not relevant to the program.");
    await refreshData();
  } catch (error) {
    showToast(error.message, true);
    button.disabled = false;
  }
}

async function handleEvidenceReviewClick(event) {
  const button = event.target.closest("[data-evidence-action]");
  if (!button) return;
  const card = button.closest("[data-assessment-id]");
  const id = card.dataset.assessmentId;
  if (button.dataset.evidenceAction === "edit") {
    $(".evidence-assessment-form", card).hidden = false;
    refreshIcons();
    return;
  }
  if (button.dataset.evidenceAction === "close") {
    $(".evidence-assessment-form", card).hidden = true;
    return;
  }
  if (button.dataset.evidenceAction === "cancel-not-relevant") {
    $(".evidence-not-relevant-form", card).hidden = true;
    return;
  }
  if (button.dataset.evidenceAction === "not_relevant") {
    $(".evidence-assessment-form", card).hidden = true;
    $(".evidence-not-relevant-form", card).hidden = false;
    refreshIcons();
    return;
  }
  if (button.dataset.evidenceAction === "accept") {
    await submitEvidenceDecision(id, {
      action: "accept",
      suggestion_identity: card.dataset.suggestionIdentity,
    });
  }
}

function handleEvidenceReviewChange(event) {
  if (event.target.name !== "classification") return;
  const form = event.target.closest(".evidence-assessment-form");
  if (form) syncDirectionField(form, event.target.value);
}

async function handleEvidenceReviewSubmit(event) {
  if (event.target.matches(".evidence-not-relevant-form")) {
    event.preventDefault();
    const form = event.target;
    const card = form.closest("[data-assessment-id]");
    await submitEvidenceDecision(card.dataset.assessmentId, {
      action: "not_relevant",
      suggestion_identity: card.dataset.suggestionIdentity,
      feedback: form.elements.feedback.value.trim(),
    });
    return;
  }
  if (!event.target.matches(".evidence-assessment-form")) return;
  event.preventDefault();
  const form = event.target;
  const submitter = event.submitter;
  const action = submitter.value;
  const body = {
    action,
    suggestion_identity: form.closest("[data-assessment-id]").dataset.suggestionIdentity,
    classification: checkedValue(form, "classification"),
    result_direction: checkedValue(form, "result_direction") || null,
    mapping_ids: selectedMappingIds(form),
    feedback: form.elements.feedback.value.trim(),
    language_issue_types: $$('input[name="language_issue_types"]:checked', form).map((input) => input.value),
  };
  if (body.language_issue_types.includes("other_language_issue") && !body.feedback) {
    showToast("Describe the other language issue in the comments field.", true);
    return;
  }
  await submitEvidenceDecision(form.closest("[data-assessment-id]").dataset.assessmentId, body);
}

async function submitEvidenceDecision(id, body) {
  try {
    await api(`/api/evidence-assessments/${encodeURIComponent(id)}/decision`, { method: "POST", body });
    showToast(body.action === "revise" ? "Mapping suggestion revised." : "Evidence assessment recorded.");
    await refreshData();
  } catch (error) {
    showToast(error.message, true);
  }
}

async function api(path, options = {}) {
  const headers = options.body ? { "Content-Type": "application/json" } : {};
  const response = await fetch(path, {
    method: options.method || "GET",
    headers,
    credentials: "same-origin",
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  let payload;
  try {
    payload = await response.json();
  } catch {
    throw new Error("The server returned an unreadable response.");
  }
  if (!response.ok) {
    if (response.status === 401 && !options.anonymous) {
      clearSession();
      showLogin();
    }
    throw new Error(payload.error || `Request failed (${response.status}).`);
  }
  return payload;
}

function emptyState(icon, message) {
  return `<div class="empty-state"><div><i data-lucide="${icon}" aria-hidden="true"></i><p>${escapeHtml(message)}</p></div></div>`;
}

function renderLoadFailure(selector, message) {
  $(selector).innerHTML = emptyState("alert-circle", message);
  refreshIcons();
}

let toastTimer;
function showToast(message, error = false) {
  const toast = $("#toast");
  clearTimeout(toastTimer);
  toast.textContent = message;
  toast.classList.toggle("is-error", error);
  toast.hidden = false;
  toastTimer = setTimeout(() => { toast.hidden = true; }, 4500);
}

function percent(value) { return `${Math.round(Number(value) * 100)}%`; }
function titleCase(value) { return String(value || "").replace(/(^|_)([a-z])/g, (_, space, letter) => `${space ? " " : ""}${letter.toUpperCase()}`); }
function formatDate(value) { return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value)); }
function levelLabel(level) { return ({ output: "Logic Model output", enabling_output: "Enabling output", short_term_outcome: "Short-term outcome", medium_term_outcome: "Medium-term outcome", long_term_outcome: "Long-term outcome", ultimate_goal: "Ultimate goal", working_output: "Working output", working_outcome: "Working outcome" })[level] || "Logic Model element"; }
function escapeHtml(value) { return String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#039;"); }
function refreshIcons() { if (window.lucide) window.lucide.createIcons(); }
