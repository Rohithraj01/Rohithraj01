/*
 * RISE launch splash.
 *
 * Timeline:
 *   1. Thames Water logo appears
 *   2. Thames Water slides left, the DCL logo arrives on the right
 *   3. Both logos merge in the centre and become a single water drop
 *   4. The drop falls into still water: splash, circular waves, and the RISE logo
 *      spreads out from the point of impact. The waves keep rolling out.
 *   5. RISE holds until the app has finished loading in the background
 *   6. RISE enlarges and the splash opens onto the app
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
  const RISE_UP = "cubic-bezier(0.2, 0.8, 0.3, 1)"; // decelerates, like a drop at the top of its arc
  const GRAVITY = "cubic-bezier(0.55, 0, 0.9, 0.45)"; // accelerates, like a falling drop

  function create(options = {}) {
    const root = options.root || document.getElementById("splash");
    const app = options.app || document.getElementById("app");
    const minHold = options.minHold ?? 900; // ms the RISE logo stays before opening
    const statusDelay = options.statusDelay ?? 400; // show "loading" only if the app is slow
    const waveEvery = options.waveEvery ?? 650; // ms between the continuing waves

    const $ = (sel) => root.querySelector(sel);
    const stage = $(".splash__stage");
    const tw = $(".splash__logo--tw");
    const dcl = $(".splash__logo--dcl");
    const rise = $(".splash__logo--rise");
    const drop = $(".splash__drop");
    const waves = $(".splash__waves");
    const flash = $(".splash__flash");
    const status = $(".splash__status");
    const statusText = $(".splash__status-text");
    const bar = $(".splash__bar span");
    const skipBtn = $(".splash__skip");

    const reduceMotion = global.matchMedia("(prefers-reduced-motion: reduce)").matches;

    let skipped = false;
    let waveTimer = null;
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

    // --- Water effects -----------------------------------------------------

    // One circular wave expanding from the centre. Not tracked by skip(): waves are ambient.
    function spawnWave({ strength = 1, light = false, duration = 2600 } = {}) {
      const ring = document.createElement("div");
      ring.className = "splash__wave" + (light ? " splash__wave--light" : "");
      waves.appendChild(ring);
      ring.animate([
        { opacity: 0.9 * strength, transform: "scale(0.2)" },
        { opacity: 0, transform: "scale(3.8)" },
      ], { duration, easing: "cubic-bezier(0.1, 0.6, 0.3, 1)", fill: "forwards" })
        .finished.catch(() => {}).finally(() => ring.remove());
    }

    function startWaves() {
      let n = 0;
      waveTimer = setInterval(() => spawnWave({ strength: 0.75, light: n++ % 2 === 1 }), waveEvery);
    }

    function stopWaves() {
      clearInterval(waveTimer);
      waveTimer = null;
    }

    // Small droplets thrown up where the drop hits the water
    function splashDroplets(count = 9) {
      const size = stage.offsetWidth;
      for (let i = 0; i < count; i++) {
        const dot = document.createElement("div");
        dot.className = "splash__droplet";
        waves.appendChild(dot);
        const angle = Math.PI * (0.15 + 0.7 * (i / (count - 1))) + (Math.random() - 0.5) * 0.25;
        const reach = size * (0.22 + Math.random() * 0.2);
        const dx = -Math.cos(angle) * reach;
        const up = Math.sin(angle) * reach * 1.1;
        const s = 0.6 + Math.random() * 0.8;
        dot.animate([
          { opacity: 1, transform: `translate(0, 0) scale(${s})` },
          { opacity: 1, transform: `translate(${dx * 0.7}px, ${-up}px) scale(${s})`, offset: 0.45, easing: GRAVITY },
          { opacity: 0, transform: `translate(${dx}px, ${up * 0.15}px) scale(${s * 0.6})` },
        ], { duration: 750 + Math.random() * 250, easing: RISE_UP, fill: "forwards" })
          .finished.catch(() => {}).finally(() => dot.remove());
      }
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
      const off = tw.offsetWidth * 0.62;
      await Promise.all([
        animate(tw, [
          { opacity: 1, transform: "translateX(0) scale(1)" },
          { opacity: 1, transform: `translateX(${-off}px) scale(0.85)` },
        ], { duration: 900 }),
        animate(dcl, [
          { opacity: 0, transform: `translateX(${off * 2.6}px) scale(0.6)` },
          { opacity: 1, transform: `translateX(${off}px) scale(0.85)` },
        ], { duration: 900 }),
      ]);
      await wait(650);
    }

    // Both logos slide together and melt into a single drop of water
    async function mergeIntoDrop() {
      const off = tw.offsetWidth * 0.62;
      const converge = { duration: 700, easing: EASE_IN };
      await Promise.all([
        animate(tw, [
          { opacity: 1, transform: `translateX(${-off}px) scale(0.85)` },
          { opacity: 1, transform: "translateX(0) scale(0.4)", offset: 0.75 },
          { opacity: 0, transform: "translateX(0) scale(0.08)" },
        ], converge),
        animate(dcl, [
          { opacity: 1, transform: `translateX(${off}px) scale(0.85)` },
          { opacity: 1, transform: "translateX(0) scale(0.4)", offset: 0.75 },
          { opacity: 0, transform: "translateX(0) scale(0.08)" },
        ], converge),
        animate(drop, [
          { opacity: 0, transform: "scale(0.1)" },
          { opacity: 1, transform: "scale(1.15)", offset: 0.7 },
          { opacity: 1, transform: "scale(1)" },
        ], { duration: 450, delay: 450, easing: EASE_OUT }),
      ]);
    }

    // The drop is lifted, then falls into still water
    async function dropFalls() {
      const lift = stage.offsetWidth * 0.95;
      await animate(drop, [
        { transform: "translateY(0) scale(1)", easing: RISE_UP },
        { transform: `translateY(${-lift}px) scale(0.95, 1)`, offset: 0.42, easing: GRAVITY },
        { transform: "translateY(0) scale(0.85, 1.3)" },
      ], { duration: 950, easing: "linear" });
      // Impact: the drop flattens into the surface
      animate(drop, [
        { opacity: 1, transform: "translateY(0) scale(0.85, 1.3)" },
        { opacity: 0, transform: "translateY(10%) scale(2, 0.15)" },
      ], { duration: 160, easing: "ease-out" });
    }

    // Ripples from the impact, and RISE spreads out across the water from the same point
    async function riseFromRipples() {
      animate(flash, [
        { opacity: 0, transform: "scale(0.2)" },
        { opacity: 1, transform: "scale(0.8)", offset: 0.25 },
        { opacity: 0, transform: "scale(1.8)" },
      ], { duration: 900 });
      splashDroplets();
      spawnWave({ strength: 1, duration: 2200 });
      setTimeout(() => spawnWave({ strength: 0.9, light: true, duration: 2400 }), 160);
      setTimeout(() => spawnWave({ strength: 0.8, duration: 2600 }), 340);
      startWaves();

      await animate(rise, [
        { opacity: 0, transform: "scale(0.35)", clipPath: "circle(0% at 50% 50%)" },
        { opacity: 1, transform: "scale(1.06)", clipPath: "circle(45% at 50% 50%)", offset: 0.5 },
        { opacity: 1, transform: "scale(0.97)", clipPath: "circle(75% at 50% 50%)", offset: 0.75 },
        { opacity: 1, transform: "scale(1)", clipPath: "circle(75% at 50% 50%)" },
      ], { duration: 1300, delay: 60, easing: EASE_OUT });
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
      stopWaves();
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
        await mergeIntoDrop();
        await dropFalls();
        await riseFromRipples();
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
