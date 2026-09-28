"use strict";
// The consent page's script. It lives here, not in an inline <script>, because /oauth/ is served with
// script-src 'self'. Vite copies frontend/public/ verbatim into static/, so this is served at /oauth.js
// with no build step — plain browser JavaScript, no framework, no bundler.
(function () {
  var alertBox = document.querySelector('[role="alert"]');
  var phone = document.getElementById("phone");
  var code = document.getElementById("code");
  var smsForm = document.getElementById("smsForm");
  var codeForm = document.getElementById("codeForm");
  var sendCode = document.getElementById("sendCode");
  var signIn = document.getElementById("signIn");
  var pwForm = document.getElementById("pwForm");
  var signOut = document.getElementById("signOut");

  function say(message) {
    if (alertBox) alertBox.textContent = message || "";
  }

  // Every endpoint here is the same one the app itself uses: JSON in, JSON out, X-HRS for CSRF.
  async function post(path, body) {
    var resp;
    try {
      resp = await fetch(path, {
        method: "POST",
        headers: { "content-type": "application/json", "X-HRS": "1" },
        body: JSON.stringify(body),
      });
    } catch (err) {
      return { ok: false, error: "Couldn't reach Galleon. Check your connection and try again." };
    }
    var data = {};
    try {
      data = await resp.json();
    } catch (err) {
      data = {};
    }
    if (!resp.ok) return { ok: false, error: data.error || "Something went wrong. Try again." };
    return { ok: true, data: data };
  }

  if (smsForm) {
    smsForm.addEventListener("submit", async function (event) {
      event.preventDefault();
      say("");
      sendCode.disabled = true;
      var result = await post("/api/login/sms/start", { phone: phone.value });
      sendCode.disabled = false;
      if (!result.ok) {
        say(result.error);
        return;
      }
      codeForm.hidden = false;
      sendCode.textContent = "Text me another code";
      code.focus();
    });
  }

  if (codeForm) {
    codeForm.addEventListener("submit", async function (event) {
      event.preventDefault();
      say("");
      signIn.disabled = true;
      var result = await post("/api/login/sms/check", { phone: phone.value, code: code.value });
      signIn.disabled = false;
      if (!result.ok) {
        say(result.error);
        return;
      }
      // The cookie is set; reloading this same URL keeps every OAuth parameter and shows the consent state.
      location.reload();
    });
  }

  if (pwForm) {
    pwForm.addEventListener("submit", async function (event) {
      event.preventDefault();
      say("");
      var result = await post("/api/login", {
        username: document.getElementById("username").value,
        password: document.getElementById("password").value,
      });
      if (!result.ok) {
        say(result.error);
        return;
      }
      location.reload();
    });
  }

  if (signOut) {
    signOut.addEventListener("click", async function () {
      await post("/api/logout", {});
      location.reload();
    });
  }
})();
