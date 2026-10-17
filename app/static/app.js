const PHASES = [
  ["VERIFY_ID", "Verify", "Strict gate"],
  ["RESOLVE_INTENT", "Intent", "Open reasoning"],
  ["PROCESS_CASE", "Case", "Grounded answer"],
  ["POST_PROCESS", "Follow-up", "Email choice"],
];

const FACTOR_LABELS = {
  name: "Full name",
  dob: "Date of birth",
  phone: "Phone on file",
  email: "Email on file",
  id_last4: "SSN or ID last 4",
};

const SCRIPTS = {
  demo: "I’m the policyholder. My name is Margaret Chen, policy POL-9921. I’m calling about my denied healthcare claim from January. DOB is 1985-03-15, SSN last four is 4472.",
  angry: "I already told you who I am. This is ridiculous. Just tell me why my claim was denied.",
  scope: "What is RL?",
  rep: "This is David Chen. I’m calling on behalf of Margaret Chen about her denied healthcare claim from January.",
};

const log = document.querySelector("#log");
const draft = document.querySelector("#draft");
const send = document.querySelector("#send");
const banner = document.querySelector("#banner");
let sessionId = null;
let busy = false;
let providers = [];
/** empty | unchecked | checking | ready | error */
let keyState = "empty";
let keyError = "";
let lastChecked = "";

const providerSelect = document.querySelector("#llm-provider");
const modelInput = document.querySelector("#llm-model");
const keyInput = document.querySelector("#llm-key");
const useKeyButton = document.querySelector("#use-key");

const modelList = document.querySelector("#llm-models");
let keyNote = "";

providerSelect.addEventListener("change", () => {
  const chosen = providers.find((item) => item.id === providerSelect.value);
  if (chosen) modelInput.value = chosen.model;
  fillModels([]);
  markUnchecked();
});
modelInput.addEventListener("input", markUnchecked);
keyInput.addEventListener("input", markUnchecked);
useKeyButton.addEventListener("click", () => {
  checkKey();
});
document.querySelector("#key-form").addEventListener("submit", (event) => {
  event.preventDefault();
  checkKey();
});

document.querySelector("#composer").addEventListener("submit", (event) => {
  event.preventDefault();
  submit(draft.value);
});

draft.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    submit(draft.value);
  }
});

document.querySelector("#new-call").addEventListener("click", () => boot(true));
document.querySelectorAll("[data-script]").forEach((button) => {
  button.addEventListener("click", () => submit(SCRIPTS[button.dataset.script]));
});

boot(false);

function saveKey() {
  sessionStorage.setItem("claims.provider", providerSelect.value);
  sessionStorage.setItem("claims.model", modelInput.value.trim());
  sessionStorage.setItem("claims.key", keyInput.value);
}

function fingerprint() {
  return `${providerSelect.value}|${modelInput.value.trim()}|${keyInput.value.trim()}`;
}

function fillModels(models) {
  modelList.replaceChildren();
  models.forEach((id) => {
    const option = document.createElement("option");
    option.value = id;
    modelList.append(option);
  });
}

function markUnchecked() {
  saveKey();
  keyNote = "";
  const key = keyInput.value.trim();
  if (!key) {
    keyState = "empty";
    keyError = "";
    lastChecked = "";
  } else {
    keyState = "unchecked";
    keyError = "";
  }
  paintHealth();
}

function callerBody() {
  const key = keyInput.value.trim();
  if (!key) return {};
  return {
    llm_provider: providerSelect.value,
    llm_model: modelInput.value.trim(),
    llm_api_key: key,
  };
}

function paintHealth() {
  const node = document.querySelector("#health");
  const panel = document.querySelector("#key-panel");
  const key = keyInput.value.trim();
  const modelLabel = `${providerSelect.value} / ${modelInput.value.trim() || "default model"}`;
  panel.classList.toggle("missing", keyState === "empty" || keyState === "unchecked");
  panel.classList.toggle("error", keyState === "error");
  panel.classList.toggle("ready", keyState === "ready");
  if (keyState === "empty" || !key) {
    node.textContent = "No key yet. Live replies stay off until you paste one.";
    return;
  }
  if (keyState === "checking") {
    node.textContent = `Checking ${modelLabel}…`;
    return;
  }
  if (keyState === "error") {
    node.textContent = keyError || "That key did not work. Fix it and click Use this key again.";
    return;
  }
  if (keyState === "ready") {
    node.textContent = `${keyNote ? `${keyNote} ` : ""}Ready. This tab will use ${modelLabel}.`;
    return;
  }
  node.textContent = "Key entered. Click Use this key to confirm it works.";
}

async function checkKey() {
  saveKey();
  const key = keyInput.value.trim();
  if (!key) {
    keyState = "empty";
    keyError = "";
    paintHealth();
    return;
  }
  keyState = "checking";
  keyError = "";
  paintHealth();
  useKeyButton.disabled = true;
  try {
    const result = await fetch("/api/llm/check", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(callerBody()),
    }).then(readJson);
    fillModels(result.models || []);
    if (result.ok) {
      keyNote = result.switched
        ? `${result.requested_model || "That model"} isn't available for this key, so I switched to ${result.model}.`
        : "";
      if (result.model) modelInput.value = result.model;
      saveKey();
      keyState = "ready";
      keyError = "";
      lastChecked = fingerprint();
    } else {
      keyState = "error";
      const hint = (result.models || []).length
        ? " Click the Model field to choose one your key can use."
        : "";
      keyError = `${result.error || "That key did not work."}${hint}`;
      lastChecked = fingerprint();
    }
  } catch (error) {
    keyState = "error";
    keyError = error.message || "Could not reach the model host.";
    lastChecked = fingerprint();
  } finally {
    useKeyButton.disabled = false;
    paintHealth();
  }
}

function noteKeyFailure(result) {
  const decisions = result?.debug?.decisions || [];
  const reply = (result?.reply || "").toLowerCase();
  const failed = decisions.some((line) => /model config|phrasing_config/i.test(line))
    || reply.includes("can't reach the language model")
    || reply.includes("check llm_api_key");
  if (!failed || !keyInput.value.trim()) return;
  keyState = "error";
  keyError = "That key or model failed on the last turn. Fix it and click Use this key.";
  lastChecked = fingerprint();
  paintHealth();
}

async function loadProviders() {
  const payload = await fetch("/api/providers").then((response) => response.json());
  providers = payload.providers || [];
  providerSelect.replaceChildren();
  providers.forEach((item) => {
    const option = document.createElement("option");
    option.value = item.id;
    option.textContent = item.label;
    option.dataset.model = item.model;
    providerSelect.append(option);
  });
  const savedProvider = sessionStorage.getItem("claims.provider");
  if (savedProvider && providers.some((item) => item.id === savedProvider)) {
    providerSelect.value = savedProvider;
  }
  const chosen = providers.find((item) => item.id === providerSelect.value);
  modelInput.value = sessionStorage.getItem("claims.model") || (chosen ? chosen.model : "");
  keyInput.value = sessionStorage.getItem("claims.key") || "";
  keyState = keyInput.value.trim() ? "unchecked" : "empty";
  keyError = "";
  lastChecked = "";
}

async function boot(fresh) {
  banner.hidden = true;
  if (fresh) log.replaceChildren();
  try {
    if (!providers.length) await loadProviders();
    await fetch("/healthz").then((response) => response.json());
    paintHealth();
    const scenario = document.querySelector("#scenario").value;
    const opened = await fetch("/api/session", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ consent_scenario: scenario }),
    }).then(readJson);
    sessionId = opened.session_id;
    paint(opened, false);
  } catch (error) {
    fail(error.message || "The desk could not open a call.");
  }
}

async function submit(text) {
  const message = text.trim();
  if (!message || busy || !sessionId) return;
  busy = true;
  send.disabled = true;
  draft.value = "";
  addBubble("user", "You", message);
  try {
    const result = await fetch(`/api/session/${sessionId}/message`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, ...callerBody() }),
    }).then(readJson);
    noteKeyFailure(result);
    paint(result, true);
  } catch (error) {
    fail(error.message || "The message did not go through.");
  } finally {
    busy = false;
    send.disabled = false;
    draft.focus();
  }
}

function paint(payload, spoke) {
  if (!spoke && payload.reply) addBubble("agent", "Avery", payload.reply);
  if (spoke && payload.reply) addBubble("agent", "Avery", payload.reply);
  renderTicket(payload.state || {}, payload.debug);
  log.scrollTop = log.scrollHeight;
}

function addBubble(role, name, text) {
  const item = document.createElement("article");
  item.className = `bubble ${role}`;
  const who = document.createElement("p");
  who.className = "who-line";
  who.textContent = name;
  const body = document.createElement("p");
  body.textContent = text;
  item.append(who, body);
  log.append(item);
}

function renderTicket(state, debug) {
  const phase = state.phase || "VERIFY_ID";
  const journey = document.querySelector("#journey");
  journey.replaceChildren();
  const activeIndex = PHASES.findIndex(([id]) => id === phase);
  PHASES.forEach(([id, label, note], index) => {
    const item = document.createElement("li");
    const done = phase === "CLOSED" || (activeIndex > index && phase !== "HUMAN_HANDOFF");
    item.className = phase === "HUMAN_HANDOFF" ? "transfer" : done ? "done" : id === phase ? "active" : "";
    const title = document.createElement("strong");
    title.textContent = label;
    const caption = document.createElement("span");
    caption.textContent = phase === "HUMAN_HANDOFF" ? "Transferred" : note;
    item.append(title, caption);
    journey.append(item);
  });

  const names = {
    VERIFY_ID: "Verify identity",
    RESOLVE_INTENT: "Resolve the claim",
    PROCESS_CASE: "Work the file",
    POST_PROCESS: "Offer a summary",
    CLOSED: "Call closed",
    HUMAN_HANDOFF: "With a specialist",
  };
  document.querySelector("#phase-label").textContent = names[phase] || phase;
  const freedom = document.querySelector("#freedom");
  const freedomText = {
    strict: "Strict gate",
    flexible: "Open reasoning",
    consent: "Email choice",
    minimal: "Call closed",
    terminal: "Transferred",
  }[state.freedom] || "Strict gate";
  freedom.textContent = freedomText;
  freedom.className = `freedom ${state.freedom === "flexible" ? "open" : state.freedom || ""}`;

  const factors = state.factors || {};
  document.querySelector("#factor-count").textContent =
    state.verified ? "confirmed" : `${state.factors_matched || 0} of 3`;
  const seals = document.querySelector("#seals");
  seals.replaceChildren();
  Object.entries(FACTOR_LABELS).forEach(([key, label]) => {
    const item = document.createElement("li");
    item.className = factors[key] ? "yes" : "";
    const mark = document.createElement("i");
    mark.textContent = "✓";
    const name = document.createElement("span");
    name.textContent = label;
    item.append(name, mark);
    seals.append(item);
  });

  const who = document.querySelector("#who");
  if (state.caller_role === "representative") {
    who.textContent = `${state.representative_name || "Representative"} for ${state.policyholder_name || "the policyholder"} · consent ${state.consent_status || "not started"}`;
  } else if (state.policyholder_name) {
    who.textContent = state.policyholder_name;
  } else {
    who.textContent = state.account_located ? "Account located. Still sealing the file." : "No account confirmed yet.";
  }

  const pins = document.querySelector("#pins");
  pins.replaceChildren();
  const memory = state.memory || {};
  const hints = [...(memory.intent_hints || [])];
  const caseHints = memory.case_hints || {};
  Object.entries(caseHints).forEach(([key, value]) => hints.push(`${key}: ${value}`));
  if (memory.email_preference) hints.push(`email: ${memory.email_preference}`);
  if (!hints.length) {
    const empty = document.createElement("span");
    empty.className = "pin";
    empty.textContent = "Nothing held yet.";
    pins.append(empty);
  }
  hints.forEach((hint) => {
    const pin = document.createElement("span");
    pin.className = `pin ${memory.saved_before_verification ? "held" : ""}`;
    pin.textContent = hint;
    pins.append(pin);
  });

  const file = document.querySelector("#file");
  if (!state.verified) {
    file.textContent = "Sealed until identity is confirmed.";
  } else if (state.active_case_id) {
    const path = state.intent_path ? ` · ${state.intent_path.replaceAll("_", " ")}` : "";
    file.textContent = `Open file ${state.active_case_id}${path}.`;
  } else {
    file.textContent = "Verified. Waiting on which claim to open.";
  }

  const letters = document.querySelector("#letters");
  letters.replaceChildren();
  const outbox = state.outbox || [];
  if (!outbox.length) {
    const empty = document.createElement("p");
    empty.className = "file";
    empty.textContent = "No summary sent.";
    letters.append(empty);
  }
  outbox.forEach((letter) => {
    const card = document.createElement("article");
    card.className = "letter";
    const title = document.createElement("h4");
    title.textContent = `${letter.subject} → ${letter.to_masked}`;
    const body = document.createElement("pre");
    body.textContent = letter.body;
    card.append(title, body);
    letters.append(card);
  });

  const decisions = document.querySelector("#decisions");
  decisions.replaceChildren();
  (debug?.decisions || ["Greeting only."]).forEach((line) => {
    const item = document.createElement("li");
    item.textContent = line;
    decisions.append(item);
  });
  if (state.handoff_note) {
    const item = document.createElement("li");
    item.textContent = state.handoff_note;
    decisions.append(item);
  }
}

async function readJson(response) {
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = payload.detail;
    const message = typeof detail === "string" ? detail : "The desk could not complete that.";
    throw new Error(message);
  }
  return payload;
}

function fail(message) {
  banner.hidden = false;
  banner.textContent = message;
}
