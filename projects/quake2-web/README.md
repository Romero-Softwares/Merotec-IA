# Quake2 Web Arena

A self-contained browser arena shooter inspired by late-90s sci-fi FPS games.

This is not an official Quake II port and does not use copyrighted assets. It is
an original playable canvas game built for the Merotec workspace.

## Run

Open `index.html` directly in a browser, or serve the folder:

```bash
python -m http.server 8000
```

Then open `http://127.0.0.1:8000/`.

## Controls

- `WASD`: move
- `Mouse` or `Left/Right arrows`: aim
- `Click` or `Space`: fire
- `1`, `2`, `3` or `Q`: switch weapons
- `Shift`: sprint
- `R`: restart

## Weapons

- `Blaster`: fast, economical and reliable at medium range.
- `Escopeta`: wide close-range burst that spends more ammo.
- `Railgun`: slow precision shot with high damage and extra reactor impact.

## Objective

Clear the multi-floor arena, use ramps, stairs, portals, and elevators to reach
the reactor core, then escape through the extraction gate.

Floors now use different surface patterns: concrete plates for normal/elevated
levels, ribbed metal on ramps, stepped strips on stairs, glowing portal rings,
and cyan elevator plates.
