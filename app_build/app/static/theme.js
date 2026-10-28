/* FluxPay console — theme (light/dark) toggle, persisted per browser. */
"use strict";

(function () {
  const STORAGE = "fluxpay_theme";

  function preferredTheme() {
    const saved = localStorage.getItem(STORAGE);
    if (saved === "light" || saved === "dark") return saved;
    if (window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches) {
      return "light";
    }
    return "dark";
  }

  function applyTheme(theme) {
    document.documentElement.dataset.theme = theme;
    document.querySelector('meta[name="color-scheme"]').setAttribute("content", theme);
    const button = document.getElementById("theme-toggle");
    if (button) button.dataset.active = theme;
  }

  function currentTheme() {
    return document.documentElement.dataset.theme === "light" ? "light" : "dark";
  }

  window.fluxpayTheme = { applyTheme, currentTheme };

  document.addEventListener("DOMContentLoaded", () => {
    applyTheme(preferredTheme());
    const button = document.getElementById("theme-toggle");
    if (!button) return;
    button.addEventListener("click", () => {
      const next = currentTheme() === "light" ? "dark" : "light";
      applyTheme(next);
      localStorage.setItem(STORAGE, next);
    });
  });
})();
