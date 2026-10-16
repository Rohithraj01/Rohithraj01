/*
 * RISE launch splash.
 *
 * Timeline:
 *   1. Thames Water logo appears
 *   2. Thames Water slides left, DCL logo arrives on the right
 *   3. Both logos merge in the centre (water ripple + flash) and the RISE logo emerges
 *   4. RISE holds until the app has finished loading in the background
 *   5. RISE enlarges and the splash opens onto the app
 *
 * Usage:
 *   const splash = RiseSplash.create();          // uses #splash and #app
 *   splash.setProgress(0.4);                     // optional, 0..1
 *   await splash.play(loadEverything());         // pass the app's "ready" promise
 */
(function (global) {
  "use strict";

  const EASE_OUT = "cubic-bezier(0.2, 0.8, 0.2, 1)";
  const EASE_IN = "cubic-bezier(0.6, 0, 0.8, 0.2)";
  const EASE_BACK = "cubic-bezier(0.34, 1.56, 0.64, 1)";

  function create(options = {}) {
    const root = options.root || document.getElementById("splash");
    const app = options.app || document.getElementById("app");
    const minHold = options.minHold ?? 700; // ms the RISE logo stays before opening
    const statusDelay = options.statusDelay ?? 400; // show "loading" only if the app is slow

    const $ = (sel) => root.querySelector(sel);
    const tw = $(".splash__logo--tw");
    const dcl = $(".splash__logo--dcl");
    const rise = $(".splash__logo--rise");
    const plus = $(".splash__plus");
    const ripples = root.querySelectorAll(".splash__ripple");
    const flash = $(".splash__flash");
    const status = $(".splash__status");
    const statusText = $(".splash__status-text");
    const bar = $(".splash__bar span");
    const skipBtn = $(".splash__skip");

    const reduceMotion = global.matchMedia("(prefers-reduced-motion: reduce)").matches;

    let skipped = false;
    const running = new Set();
    const pendingWaits = new Set();

    function animate(el, keyframes, opts) {
      const anim = el.animate(keyframes, { fill: "forwards", easing: EASE_OUT, ...opts });
      running.add(anim);
      if (skipped) anim.finish();
      return anim.finished.catch(() => {}).finally(() => running.delete(anim));
    }

    function wait(ms) {
      if (skipped) return Promise.resolve();
      return new Promise((resolve) => {
        const entry = { resolve, id: setTimeout(() => { pendingWaits.delete(entry); resolve(); }, ms) };
        pendingWaits.add(entry);
      });
    }

    function skip() {
      if (skipped) return;
      skipped = true;
      running.forEach((a) => a.finish());
      pendingWaits.forEach((w) => { clearTimeout(w.id); w.resolve(); });
      pendingWaits.clear();
      if (skipBtn) skipBtn.hidden = true;
    }

    function setProgress(value) {
      if (bar) bar.style.width = Math.round(Math.min(1, Math.max(0, value)) * 100) + "%";
    }

    function setStatus(text) {
      if (statusText) statusText.textContent = text;
    }

    // --- Phases -----------------------------------------------------------

    async function introThamesWater() {
      await animate(tw, [
        { opacity: 0, transform: "scale(0.6)" },
        { opacity: 1, transform: "scale(1)" },
      ], { duration: 900, easing: EASE_BACK });
      await wait(500);
    }

    async function introDcl() {
      const off = tw.offsetWidth * 0.78;
      await Promise.all([
        animate(tw, [
          { opacity: 1, transform: "translateX(0) scale(1)" },
          { opacity: 1, transform: `translateX(${-off}px) scale(0.8)` },
        ], { duration: 900 }),
        animate(dcl, [
          { opacity: 0, transform: `translateX(${off * 2.4}px) scale(0.6)` },
          { opacity: 1, transform: `translateX(${off}px) scale(0.8)` },
        ], { duration: 900 }),
        animate(plus, [
          { opacity: 0, transform: "scale(0.4) rotate(-90deg)" },
          { opacity: 0.9, transform: "scale(1) rotate(0deg)" },
        ], { duration: 600, delay: 400 }),
      ]);
      await wait(650);
    }

    async function mergeIntoRise() {
      const off = tw.offsetWidth * 0.78;
      const converge = { duration: 650, easing: EASE_IN };
      await Promise.all([
        animate(plus, [{ opacity: 0.9 }, { opacity: 0, transform: "scale(0.2)" }], { duration: 250 }),
        animate(tw, [
          { opacity: 1, transform: `translateX(${-off}px) scale(0.8)` },
          { opacity: 1, transform: "translateX(0) scale(0.55) rotate(-25deg)", offset: 0.85 },
          { opacity: 0, transform: "translateX(0) scale(0.4) rotate(-40deg)" },
        ], converge),
        animate(dcl, [
          { opacity: 1, transform: `translateX(${off}px) scale(0.8)` },
          { opacity: 1, transform: "translateX(0) scale(0.55) rotate(25deg)", offset: 0.85 },
          { opacity: 0, transform: "translateX(0) scale(0.4) rotate(40deg)" },
        ], converge),
      ]);

      // Impact: flash + water ripples, RISE emerges from the centre
      animate(flash, [
        { opacity: 0, transform: "scale(0.4)" },
        { opacity: 1, transform: "scale(1.2)", offset: 0.3 },
        { opacity: 0, transform: "scale(2)" },
      ], { duration: 800 });
      ripples.forEach((ring, i) => animate(ring, [
        { opacity: 0.9, transform: "scale(0.5)" },
        { opacity: 0, transform: "scale(3.2)" },
      ], { duration: 1300, delay: i * 180 }));
      await animate(rise, [
        { opacity: 0, transform: "scale(0.3)" },
        { opacity: 1, transform: "scale(1)" },
      ], { duration: 950, delay: 80, easing: EASE_BACK });
    }

    async function crossfadeReduced() {
      const fade = (el, from, to) => animate(el, [{ opacity: from }, { opacity: to }], { duration: 350 });
      await fade(tw, 0, 1); await wait(600); await fade(tw, 1, 0);
      await fade(dcl, 0, 1); await wait(600); await fade(dcl, 1, 0);
      await fade(rise, 0, 1);
    }

    async function holdUntilReady(ready) {
      const statusTimer = setTimeout(() => status && status.classList.add("is-visible"), statusDelay);
      try {
        // A skip should not cut the RISE moment short, so the minimum hold always applies
        await Promise.all([ready, new Promise((r) => setTimeout(r, minHold))]);
        setProgress(1);
      } finally {
        clearTimeout(statusTimer);
      }
      status && status.classList.remove("is-visible");
    }

    async function openApp() {
      if (skipBtn) skipBtn.hidden = true;
      app.removeAttribute("inert");
      app.removeAttribute("aria-hidden");
      const enlarge = reduceMotion
        ? animate(rise, [{ opacity: 1 }, { opacity: 0 }], { duration: 300 })
        : animate(rise, [
            { opacity: 1, transform: "scale(1)" },
            { opacity: 1, transform: "scale(2.2)", offset: 0.45 },
            { opacity: 0, transform: "scale(9)" },
          ], { duration: 1000, easing: EASE_IN });
      const reveal = Promise.all([
        animate(root, [{ opacity: 1 }, { opacity: 0 }], { duration: 600, delay: reduceMotion ? 0 : 400 }),
        animate(app, [
          { opacity: 0, transform: reduceMotion ? "none" : "scale(1.04)" },
          { opacity: 1, transform: "none" },
        ], { duration: 700, delay: reduceMotion ? 0 : 400 }),
      ]);
      root.classList.add("is-done");
      await Promise.all([enlarge, reveal]);
    }

    // --- Public -----------------------------------------------------------

    async function play(ready = Promise.resolve()) {
      app.setAttribute("inert", "");
      app.setAttribute("aria-hidden", "true");
      app.style.opacity = "0";
      if (skipBtn) skipBtn.addEventListener("click", skip, { once: true });

      // Loading runs in parallel with the animation; never leave a rejection unhandled
      ready = Promise.resolve(ready);
      ready.catch(() => {});

      // Don't start the show until the logos can actually be painted
      await Promise.all([tw, dcl, rise].map((img) => img.decode().catch(() => {})));

      if (reduceMotion) {
        await crossfadeReduced();
      } else {
        await introThamesWater();
        await introDcl();
        await mergeIntoRise();
      }

      try {
        await holdUntilReady(ready);
      } catch (err) {
        setStatus("Something went wrong while starting RISE. Please refresh to try again.");
        status && status.classList.add("is-visible");
        throw err;
      }

      await openApp();
      app.style.opacity = "";
      root.remove();
      document.dispatchEvent(new CustomEvent("rise:ready"));
    }

    return { play, skip, setProgress, setStatus };
  }

  global.RiseSplash = { create };
})(window);
