"""The development user administration page: one static document with its
CSS and JavaScript inline. Nothing is interpolated into it on the server;
the accounts arrive through /dev/api/users and are written into the table as
text nodes, so a username can never be parsed as markup."""

# Scripts and styles only from this document, requests only to this origin,
# and no framing (the delete button must not be clickable through a frame).
CONTENT_SECURITY_POLICY = (
    "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
    "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")

PAGE = """<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sereno: usuarios (desarrollo)</title>
<style>
  :root {
    color-scheme: light dark;
    --text: #1c1f24; --muted: #5b6370; --page: #f6f7f9; --card: #ffffff; --line: #d9dde3;
    --warn-bg: #fff4d6; --warn-text: #5c4300; --warn-line: #e6c665;
    --danger: #b3261e; --danger-text: #ffffff; --accent: #2f55d4;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --text: #e7e9ee; --muted: #a2a9b6; --page: #14161a; --card: #1d2026; --line: #343943;
      --warn-bg: #3a2f0b; --warn-text: #f5dd9a; --warn-line: #6e5a17;
      --danger: #f2877f; --danger-text: #2a0b08; --accent: #9db4ff;
    }
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; padding: 16px; background: var(--page); color: var(--text);
    font: 15px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif;
  }
  main { max-width: 760px; margin: 0 auto; }
  h1 { font-size: 20px; margin: 16px 0 4px; }
  .banner {
    background: var(--warn-bg); color: var(--warn-text); border: 1px solid var(--warn-line);
    border-radius: 8px; padding: 10px 12px;
  }
  .bar { display: flex; gap: 12px; align-items: center; justify-content: space-between;
         flex-wrap: wrap; margin: 4px 0 12px; }
  .muted { color: var(--muted); }
  #status { min-height: 22px; margin: 0 0 8px; }
  #status.error { color: var(--danger); }
  .card { background: var(--card); border: 1px solid var(--line); border-radius: 8px;
          overflow-x: auto; }
  table { width: 100%; border-collapse: collapse; }
  th, td { text-align: left; padding: 9px 12px; border-bottom: 1px solid var(--line);
           vertical-align: middle; }
  tr:last-child td { border-bottom: 0; }
  th { font-size: 12px; text-transform: uppercase; letter-spacing: .04em; color: var(--muted); }
  td.name { overflow-wrap: anywhere; font-weight: 600; }
  td.date { white-space: nowrap; font-variant-numeric: tabular-nums; }
  td.actions { white-space: nowrap; text-align: right; }
  button, select {
    font: inherit; color: inherit; background: var(--card); border: 1px solid var(--line);
    border-radius: 6px; padding: 5px 10px; min-height: 34px;
  }
  button { cursor: pointer; }
  button:focus-visible, select:focus-visible { outline: 2px solid var(--accent);
                                               outline-offset: 1px; }
  button.danger { border-color: var(--danger); color: var(--danger); }
  button.confirm { background: var(--danger); border-color: var(--danger);
                   color: var(--danger-text); }
  button:disabled, select:disabled { opacity: .55; cursor: default; }
  .empty { padding: 18px 12px; }
  @media (max-width: 520px) {
    body { padding: 10px; }
    thead { display: none; }
    tr { display: grid; grid-template-columns: 1fr auto; gap: 6px 10px; padding: 10px 12px;
         border-bottom: 1px solid var(--line); }
    tr:last-child { border-bottom: 0; }
    td { padding: 0; border: 0; }
    td.name { grid-column: 1 / -1; }
    td.date { grid-column: 1 / -1; color: var(--muted); }
    td.actions { text-align: right; }
  }
</style>
</head>
<body>
<main>
  <p class="banner" role="note"><strong>Herramienta de desarrollo.</strong>
    No pide sesión y solo responde en este equipo. No debe activarse fuera de una máquina de
    desarrollo.</p>
  <h1>Usuarios</h1>
  <div class="bar">
    <span id="count" class="muted"></span>
    <button id="reload" type="button">Actualizar</button>
  </div>
  <p id="status" role="status" aria-live="polite"></p>
  <div class="card">
    <table>
      <thead>
        <tr><th scope="col">Usuario</th><th scope="col">Registro (UTC)</th>
            <th scope="col">Rol</th><th scope="col"><span class="muted">Acciones</span></th></tr>
      </thead>
      <tbody id="rows"></tbody>
    </table>
    <p id="empty" class="empty muted" hidden>No hay usuarios registrados.</p>
  </div>
</main>
<script>
"use strict";
const API = "/dev/api/users";
const ROLE_LABELS = { user: "Usuario", admin: "Administrador" };
const ERRORS = {
  not_found: "El usuario ya no existe.",
  invalid: "El rol indicado no es válido.",
  forbidden: "La solicitud fue rechazada.",
  server: "El servidor no pudo completar la operación."
};
const rows = document.getElementById("rows");
const status = document.getElementById("status");

function say(message, isError) {
  status.textContent = message;
  status.className = isError ? "error" : "";
}

// Every change carries the custom header the server requires; a page from
// another origin cannot add it without a preflight the server never grants.
async function call(method, path, body) {
  const options = { method: method, headers: { "X-Sereno-Dev": "1" } };
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  const response = await fetch(path, options);
  let data = {};
  try { data = await response.json(); } catch (error) { data = {}; }
  if (!response.ok) {
    throw new Error(ERRORS[data.error] || "Error inesperado (" + response.status + ").");
  }
  return data;
}

function userPath(username) {
  return API + "/" + encodeURIComponent(username);
}

function cell(className, text) {
  const td = document.createElement("td");
  td.className = className;
  if (text !== undefined) { td.textContent = text; }
  return td;
}

function roleControl(user) {
  const select = document.createElement("select");
  select.setAttribute("aria-label", "Rol de " + user.username);
  for (const role of Object.keys(ROLE_LABELS)) {
    const option = document.createElement("option");
    option.value = role;
    option.textContent = ROLE_LABELS[role];
    select.appendChild(option);
  }
  select.value = user.role;
  select.addEventListener("change", async function () {
    select.disabled = true;
    try {
      const data = await call("PUT", userPath(user.username) + "/role", { role: select.value });
      user.role = data.user.role;
      say("Rol de " + user.username + " actualizado a " + ROLE_LABELS[user.role] + ".");
    } catch (error) {
      say(error.message, true);
    }
    select.value = user.role;
    select.disabled = false;
  });
  return select;
}

// Deleting takes two clicks: the first one only asks.
function deleteControl(user) {
  const box = document.createElement("span");
  const ask = document.createElement("button");
  ask.type = "button";
  ask.className = "danger";
  ask.textContent = "Eliminar";
  ask.setAttribute("aria-label", "Eliminar a " + user.username);
  const confirm = document.createElement("button");
  confirm.type = "button";
  confirm.className = "confirm";
  confirm.textContent = "Confirmar eliminación";
  const cancel = document.createElement("button");
  cancel.type = "button";
  cancel.textContent = "Cancelar";
  function show(asking) {
    ask.hidden = asking;
    confirm.hidden = !asking;
    cancel.hidden = !asking;
  }
  ask.addEventListener("click", function () { show(true); cancel.focus(); });
  cancel.addEventListener("click", function () { show(false); ask.focus(); });
  confirm.addEventListener("click", async function () {
    confirm.disabled = true;
    cancel.disabled = true;
    try {
      await call("DELETE", userPath(user.username));
      say("Usuario " + user.username + " eliminado.");
      await load(true);
    } catch (error) {
      say(error.message, true);
      confirm.disabled = false;
      cancel.disabled = false;
      show(false);
    }
  });
  show(false);
  box.append(ask, " ", confirm, " ", cancel);
  return box;
}

function render(users) {
  rows.replaceChildren();
  for (const user of users) {
    const tr = document.createElement("tr");
    const role = cell("role");
    role.appendChild(roleControl(user));
    const actions = cell("actions");
    actions.appendChild(deleteControl(user));
    tr.append(cell("name", user.username), cell("date", user.createdAt.replace("T", " ")),
              role, actions);
    rows.appendChild(tr);
  }
  document.getElementById("empty").hidden = users.length > 0;
  document.getElementById("count").textContent =
    users.length === 1 ? "1 usuario" : users.length + " usuarios";
}

async function load(keepMessage) {
  try {
    const data = await call("GET", API);
    render(data.users);
    if (!keepMessage) { say(""); }
  } catch (error) {
    say(error.message, true);
  }
}

document.getElementById("reload").addEventListener("click", function () { load(false); });
load(false);
</script>
</body>
</html>
"""
