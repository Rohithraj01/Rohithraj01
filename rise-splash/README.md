# RISE launch splash

Animated start-up screen for the RISE app (Rebuilding Infrastructure for Smart Engagement).
The app loads in the background while the animation plays.

## Sequence

1. **Thames Water** logo appears
2. Thames Water moves left and the **DCL** logo arrives on the right
3. The two logos merge in the centre and become a single water drop
4. The drop falls into still water: a splash, circular waves, and the **RISE** logo spreads out from the point of impact. The waves keep rolling while RISE is on screen
5. RISE stays on screen until the app has finished loading. If loading takes a while, a progress bar is shown
6. RISE **enlarges** and the splash fades to reveal the app

A **Skip** button jumps straight to the RISE logo. Users who set "reduce motion" get a simple crossfade.
The app icon (favicon, home-screen icon, PWA manifest) is the RISE logo.

## Run it

```bash
cd rise-splash
npx http-server -p 8080    # then open http://localhost:8080
```

## Use it in your app

```html
<link rel="stylesheet" href="splash.css">
<div id="splash" class="splash"> ...splash markup from index.html... </div>
<div id="app"> ...your app... </div>
<script src="splash.js"></script>
<script>
  const splash = RiseSplash.create();       // finds #splash and #app
  splash.setStatus("Loading your data…");   // optional
  splash.setProgress(0.5);                  // optional, 0..1
  splash.play(loadEverything());            // a promise that resolves when the app is ready
  document.addEventListener("rise:ready", () => { /* splash is gone */ });
</script>
```

Options for `create()`: `root`, `app`, `minHold` (how long RISE stays before opening, in ms; default 900), `waveEvery` (ms between waves; default 650),
and `statusDelay` (how long to wait before showing the loading status, in ms; default 400).

## Assets

| File | Notes |
|------|-------|
| `assets/thames-water.png` | Thames Water logo, cut out as a circle |
| `assets/dcl.png` | Donavalli Consulting (DCL) logo on a round white badge |
| `assets/rise.png` | RISE logo |
| `assets/icons/rise-*.png` | App icons generated from the RISE logo |
