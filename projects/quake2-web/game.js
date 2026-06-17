(() => {
  "use strict";

  const canvas = document.getElementById("game");
  const ctx = canvas.getContext("2d");
  const overlay = document.getElementById("overlay");
  const playButton = document.getElementById("playButton");
  const startButton = document.getElementById("startButton");
  const restartButton = document.getElementById("restartButton");
  const message = document.getElementById("message");
  const weapon = document.querySelector(".weapon");
  const hud = {
    health: document.getElementById("health"),
    armor: document.getElementById("armor"),
    ammo: document.getElementById("ammo"),
    weaponName: document.getElementById("weaponName"),
    kills: document.getElementById("kills"),
    floor: document.getElementById("floor"),
  };

  const TILE = 1;
  const FOV = Math.PI / 3;
  const MAX_DEPTH = 18;
  const ENEMY_TOTAL = 12;
  const TWO_PI = Math.PI * 2;
  const keys = new Set();
  const weapons = [
    {
      id: "blaster",
      name: "Blaster",
      ammoCost: 1,
      delay: 0.16,
      range: 13,
      cone: 0.09,
      damage: 34,
      tankDamage: 28,
      pellets: 1,
      particle: "#f4b648",
      message: "Blaster rapido equipado.",
    },
    {
      id: "shotgun",
      name: "Escopeta",
      ammoCost: 3,
      delay: 0.52,
      range: 6.4,
      cone: 0.2,
      damage: 16,
      tankDamage: 14,
      pellets: 7,
      particle: "#ff7a3d",
      message: "Escopeta de curto alcance equipada.",
    },
    {
      id: "railgun",
      name: "Railgun",
      ammoCost: 5,
      delay: 0.78,
      range: 16,
      cone: 0.045,
      damage: 92,
      tankDamage: 76,
      pellets: 1,
      particle: "#5fd7ff",
      message: "Railgun precisa equipada.",
    },
  ];

  const level = [
    "####################",
    "#..r.#.......P.....#",
    "#..R.#..##..###....#",
    "#....s..##....#....#",
    "###..#..S.....#..###",
    "#....#..####..#....#",
    "#...P...#..#...L...#",
    "#..###..#..#..###..#",
    "#..L....#..#...p...#",
    "#....#..####..#....#",
    "###..#..s.....#..###",
    "#....#....##..l....#",
    "#.p..###..##..#....#",
    "#E...R.......l.....#",
    "####################",
  ];

  const wallPalette = {
    "#": ["#46515a", "#2d353c"],
    "@": ["#75543a", "#4b3325"],
    "%": ["#3f6559", "#254238"],
  };

  const tileColors = {
    "#": "#58636b",
    ".": "#151a1d",
    "E": "#30412e",
    r: "#736137",
    R: "#9b7b38",
    s: "#405f76",
    S: "#5f89a8",
    P: "#8c5cff",
    p: "#8c5cff",
    L: "#5fd7ff",
    l: "#5fd7ff",
  };

  const floorPalettes = {
    ground: { base: "#242526", line: "#33383b", accent: "#1a1c1e" },
    mid: { base: "#303a3e", line: "#46545a", accent: "#222a2d" },
    high: { base: "#3a3832", line: "#5b5546", accent: "#25231f" },
    ramp: { base: "#6f633f", line: "#c19b43", accent: "#3f3928" },
    stairs: { base: "#405c70", line: "#79a4bd", accent: "#243947" },
    portal: { base: "#35265d", line: "#9d6cff", accent: "#19112c" },
    elevator: { base: "#24434d", line: "#66dfff", accent: "#10262e" },
    exit: { base: "#294129", line: "#7cf27a", accent: "#142414" },
  };

  const heightZones = [
    { x1: 12, y1: 1, x2: 18.95, y2: 4.95, z: 1.25 },
    { x1: 1, y1: 9, x2: 5.95, y2: 13.95, z: 0.85 },
    { x1: 10, y1: 10, x2: 18.95, y2: 13.95, z: 1.1 },
  ];

  const portalPairs = [
    { a: { x: 13.5, y: 1.5 }, b: { x: 15.5, y: 8.5 } },
    { a: { x: 4.5, y: 6.5 }, b: { x: 2.5, y: 12.5 } },
  ];

  const elevatorPairs = [
    { a: { x: 15.5, y: 6.5, z: 0 }, b: { x: 14.5, y: 13.5, z: 1.1 } },
    { a: { x: 3.5, y: 8.5, z: 0 }, b: { x: 14.5, y: 11.5, z: 1.1 } },
  ];

  const playerStart = { x: 2.4, y: 12.5, angle: -0.45 };
  const player = {
    x: playerStart.x,
    y: playerStart.y,
    angle: playerStart.angle,
    health: 100,
    armor: 50,
    ammo: 80,
    kills: 0,
    fireDelay: 0,
    damageFlash: 0,
    z: 0,
    targetZ: 0,
    portalCooldown: 0,
    portalLock: null,
    elevatorCooldown: 0,
    elevatorLock: null,
    elevatorRide: null,
    weaponIndex: 0,
    alive: true,
    won: false,
  };

  const exit = { x: 1.5, y: 13.5, open: false };
  const reactor = { x: 17.35, y: 1.8, hp: 7, shield: true, destroyed: false };
  let running = false;
  let lastTime = 0;
  let shake = 0;
  let bannerTimer = 0;

  let enemies = [];
  let pickups = [];
  let particles = [];
  let depthBuffer = [];

  function makeEnemies() {
    return [
      { x: 6.5, y: 2.3, hp: 48, speed: 0.75, attack: 10, kind: "trooper", cool: 0 },
      { x: 11.4, y: 3.6, hp: 48, speed: 0.75, attack: 10, kind: "trooper", cool: 0 },
      { x: 16.2, y: 3.2, hp: 64, speed: 0.55, attack: 14, kind: "gunner", cool: 0 },
      { x: 4.4, y: 5.7, hp: 48, speed: 0.78, attack: 10, kind: "trooper", cool: 0 },
      { x: 15.5, y: 5.6, hp: 64, speed: 0.58, attack: 14, kind: "gunner", cool: 0 },
      { x: 7.4, y: 7.5, hp: 44, speed: 0.95, attack: 8, kind: "runner", cool: 0 },
      { x: 12.6, y: 7.5, hp: 44, speed: 0.95, attack: 8, kind: "runner", cool: 0 },
      { x: 4.4, y: 9.5, hp: 48, speed: 0.78, attack: 10, kind: "trooper", cool: 0 },
      { x: 15.4, y: 9.4, hp: 64, speed: 0.58, attack: 14, kind: "gunner", cool: 0 },
      { x: 3.5, y: 12.3, hp: 44, speed: 0.92, attack: 8, kind: "runner", cool: 0 },
      { x: 12.6, y: 12.5, hp: 48, speed: 0.76, attack: 10, kind: "trooper", cool: 0 },
      { x: 17.1, y: 12.3, hp: 120, speed: 0.48, attack: 18, kind: "tank", cool: 0 },
    ].map(snapEnemySpawn);
  }

  function makePickups() {
    return [
      { x: 3.5, y: 1.8, type: "ammo", taken: false },
      { x: 15.2, y: 2.5, type: "health", taken: false },
      { x: 2.4, y: 6.6, type: "armor", taken: false },
      { x: 17.3, y: 6.6, type: "ammo", taken: false },
      { x: 4.8, y: 10.6, type: "health", taken: false },
      { x: 13.5, y: 10.5, type: "ammo", taken: false },
    ];
  }

  function resetGame() {
    Object.assign(player, {
      x: playerStart.x,
      y: playerStart.y,
      angle: playerStart.angle,
      health: 100,
      armor: 50,
      ammo: 80,
      kills: 0,
      fireDelay: 0,
      damageFlash: 0,
      z: 0,
      targetZ: 0,
      portalCooldown: 0,
      portalLock: null,
      elevatorCooldown: 0,
      elevatorLock: null,
      elevatorRide: null,
      weaponIndex: 0,
      alive: true,
      won: false,
    });
    reactor.hp = 7;
    reactor.shield = true;
    reactor.destroyed = false;
    exit.open = false;
    enemies = makeEnemies();
    pickups = makePickups();
    particles = [];
    shake = 0;
    bannerTimer = 4;
    setMessage("Infiltre o complexo em andares: use rampas, escadas, portais e elevadores.");
    updateHud();
  }

  function lockPointer() {
    if (!canvas.requestPointerLock) return;
    const lockResult = canvas.requestPointerLock();
    if (lockResult && typeof lockResult.catch === "function") {
      lockResult.catch(() => {});
    }
  }

  function startGame() {
    overlay.classList.add("hidden");
    running = true;
    resetGame();
    canvas.focus();
    lockPointer();
  }

  function setMessage(text) {
    message.textContent = text;
    bannerTimer = 3.4;
  }

  function updateHud() {
    hud.health.textContent = String(Math.max(0, Math.ceil(player.health)));
    hud.armor.textContent = String(Math.max(0, Math.ceil(player.armor)));
    hud.ammo.textContent = String(player.ammo);
    hud.weaponName.textContent = currentWeapon().name;
    hud.kills.textContent = `${player.kills}/${ENEMY_TOTAL}`;
    hud.floor.textContent = currentFloorName();
    weapon.dataset.weapon = currentWeapon().id;
  }

  function currentWeapon() {
    return weapons[player.weaponIndex] || weapons[0];
  }

  function selectWeapon(index, announce = true) {
    const nextIndex = (index + weapons.length) % weapons.length;
    if (player.weaponIndex === nextIndex) return;
    player.weaponIndex = nextIndex;
    player.fireDelay = Math.max(player.fireDelay, 0.08);
    if (announce) setMessage(currentWeapon().message);
    updateHud();
  }

  function cycleWeapon() {
    selectWeapon(player.weaponIndex + 1);
  }

  function currentFloorName() {
    if (player.z > 0.95) return "2";
    if (player.z > 0.35) return "1";
    return "0";
  }

  function normalizeAngle(angle) {
    angle %= TWO_PI;
    return angle < 0 ? angle + TWO_PI : angle;
  }

  function angleDelta(a, b) {
    let diff = normalizeAngle(a) - normalizeAngle(b);
    if (diff > Math.PI) diff -= TWO_PI;
    if (diff < -Math.PI) diff += TWO_PI;
    return diff;
  }

  function tileAt(x, y) {
    const row = level[Math.floor(y)];
    if (!row) return "#";
    return row[Math.floor(x)] || "#";
  }

  function isWall(x, y) {
    return tileAt(x, y) === "#";
  }

  function zoneHeightAt(x, y) {
    const zone = heightZones.find((entry) => x >= entry.x1 && x <= entry.x2 && y >= entry.y1 && y <= entry.y2);
    return zone ? zone.z : 0;
  }

  function floorHeightAt(x, y) {
    const tile = tileAt(x, y);
    if (tile === "#") return 0;
    const zoneHeight = zoneHeightAt(x, y);
    if (tile === "r") return Math.max(zoneHeight, 0.32);
    if (tile === "R") return Math.max(zoneHeight, 0.74);
    if (tile === "s") return Math.max(zoneHeight, 0.46);
    if (tile === "S") return Math.max(zoneHeight, 0.92);
    if (tile === "l") return Math.max(zoneHeight, 1.1);
    if (tile === "L") return 0;
    return zoneHeight;
  }

  function terrainLabelAt(x, y) {
    const tile = tileAt(x, y);
    if (tile === "r" || tile === "R") return "Rampa";
    if (tile === "s" || tile === "S") return "Escadaria";
    if (tile === "P" || tile === "p") return "Portal";
    if (tile === "L" || tile === "l") return "Elevador";
    return "";
  }

  function floorPaletteAt(x, y) {
    const tile = tileAt(x, y);
    if (tile === "#") return null;
    if (tile === "r" || tile === "R") return floorPalettes.ramp;
    if (tile === "s" || tile === "S") return floorPalettes.stairs;
    if (tile === "P" || tile === "p") return floorPalettes.portal;
    if (tile === "L" || tile === "l") return floorPalettes.elevator;
    if (tile === "E") return floorPalettes.exit;
    const z = floorHeightAt(x, y);
    if (z > 0.95) return floorPalettes.high;
    if (z > 0.35) return floorPalettes.mid;
    return floorPalettes.ground;
  }

  function texturedFloorColor(x, y, distance) {
    const palette = floorPaletteAt(x, y);
    if (!palette) return null;
    const tile = tileAt(x, y);
    const localX = x - Math.floor(x);
    const localY = y - Math.floor(y);
    const shade = Math.max(0.2, 1 - distance / (MAX_DEPTH * 0.95));
    let color = palette.base;

    if (tile === "r" || tile === "R") {
      const stripe = Math.floor((localX + localY) * 8) % 2 === 0;
      const edge = localX < 0.08 || localY < 0.08 || localX > 0.92 || localY > 0.92;
      color = edge ? palette.line : stripe ? palette.base : palette.accent;
    } else if (tile === "s" || tile === "S") {
      const tread = Math.floor(localY * 5) % 2 === 0;
      const riser = localY % 0.2 < 0.035;
      color = riser ? palette.line : tread ? palette.base : palette.accent;
    } else if (tile === "P" || tile === "p") {
      const ring = Math.hypot(localX - 0.5, localY - 0.5);
      color = ring > 0.34 && ring < 0.46 ? palette.line : Math.floor((localX - localY) * 9) % 2 ? palette.base : palette.accent;
    } else if (tile === "L" || tile === "l") {
      const seam = localX < 0.08 || localY < 0.08 || localX > 0.92 || localY > 0.92 || Math.abs(localX - 0.5) < 0.035;
      color = seam ? palette.line : Math.floor((localX + localY) * 4) % 2 ? palette.base : palette.accent;
    } else {
      const plate = localX < 0.045 || localY < 0.045 || localX > 0.955 || localY > 0.955;
      const scuff = Math.floor((x * 3.1 + y * 5.7) * 2) % 5 === 0;
      color = plate ? palette.line : scuff ? palette.accent : palette.base;
    }

    return colorWithShade(color, shade);
  }

  function hasSpawnClearance(x, y, radius = 0.24) {
    return [
      [0, 0],
      [radius, 0],
      [-radius, 0],
      [0, radius],
      [0, -radius],
      [radius, radius],
      [-radius, radius],
      [radius, -radius],
      [-radius, -radius],
    ].every(([offsetX, offsetY]) => !isWall(x + offsetX, y + offsetY));
  }

  function snapEnemySpawn(enemy) {
    if (hasSpawnClearance(enemy.x, enemy.y)) return enemy;

    const originX = Math.floor(enemy.x);
    const originY = Math.floor(enemy.y);
    let best = null;

    for (let radius = 0; radius <= 4; radius += 1) {
      for (let y = originY - radius; y <= originY + radius; y += 1) {
        for (let x = originX - radius; x <= originX + radius; x += 1) {
          const candidateX = x + 0.5;
          const candidateY = y + 0.5;
          if (!hasSpawnClearance(candidateX, candidateY)) continue;
          const distance = Math.hypot(candidateX - enemy.x, candidateY - enemy.y);
          if (!best || distance < best.distance) {
            best = { x: candidateX, y: candidateY, distance };
          }
        }
      }
      if (best) return { ...enemy, x: best.x, y: best.y };
    }

    return enemy;
  }

  function hasLineOfSight(x1, y1, x2, y2) {
    const dx = x2 - x1;
    const dy = y2 - y1;
    const distance = Math.hypot(dx, dy);
    const steps = Math.ceil(distance / 0.08);
    for (let i = 1; i < steps; i += 1) {
      const t = i / steps;
      if (isWall(x1 + dx * t, y1 + dy * t)) return false;
    }
    return true;
  }

  function tryMove(entity, dx, dy) {
    const radius = 0.22;
    const nextX = entity.x + dx;
    const nextY = entity.y + dy;

    if (!isWall(nextX + Math.sign(dx) * radius, entity.y) && !isWall(nextX, entity.y - radius) && !isWall(nextX, entity.y + radius)) {
      entity.x = nextX;
    }
    if (!isWall(entity.x - radius, nextY) && !isWall(entity.x + radius, nextY) && !isWall(entity.x, nextY + Math.sign(dy) * radius)) {
      entity.y = nextY;
    }
  }

  function nearestPortal(x, y) {
    for (const pair of portalPairs) {
      if (Math.hypot(x - pair.a.x, y - pair.a.y) < 0.42) return { from: pair.a, to: pair.b };
      if (Math.hypot(x - pair.b.x, y - pair.b.y) < 0.42) return { from: pair.b, to: pair.a };
    }
    return null;
  }

  function nearestElevator(x, y) {
    for (const pair of elevatorPairs) {
      if (Math.hypot(x - pair.a.x, y - pair.a.y) < 0.48) return { from: pair.a, to: pair.b };
      if (Math.hypot(x - pair.b.x, y - pair.b.y) < 0.48) return { from: pair.b, to: pair.a };
    }
    return null;
  }

  function updateTraversal(dt) {
    if (player.portalCooldown > 0) player.portalCooldown -= dt;
    if (player.elevatorCooldown > 0) player.elevatorCooldown -= dt;

    if (player.elevatorRide) {
      player.elevatorRide.progress += dt / 1.2;
      const t = Math.min(1, player.elevatorRide.progress);
      const ease = t * t * (3 - 2 * t);
      player.z = player.elevatorRide.startZ + (player.elevatorRide.endZ - player.elevatorRide.startZ) * ease;
      player.targetZ = player.z;
      if (t >= 1) {
        player.x = player.elevatorRide.to.x;
        player.y = player.elevatorRide.to.y;
        player.z = player.elevatorRide.endZ;
        player.targetZ = player.elevatorRide.endZ;
        player.elevatorRide = null;
        player.elevatorCooldown = 1.4;
        setMessage(`Elevador chegou ao andar ${currentFloorName()}.`);
      }
      updateHud();
      return;
    }

    const portal = nearestPortal(player.x, player.y);
    if (!portal) player.portalLock = null;
    const portalLocked = portal && player.portalLock && Math.hypot(portal.from.x - player.portalLock.x, portal.from.y - player.portalLock.y) < 0.1;
    if (portal && !portalLocked && player.portalCooldown <= 0) {
      player.x = portal.to.x;
      player.y = portal.to.y;
      player.z = floorHeightAt(player.x, player.y);
      player.targetZ = player.z;
      player.portalCooldown = 1.1;
      player.portalLock = { x: portal.to.x, y: portal.to.y };
      shake = Math.max(shake, 6);
      spawnParticle(player.x, player.y, "#8c5cff", 0.35, 28);
      setMessage(`Portal ativo: translocado para o andar ${currentFloorName()}.`);
      updateHud();
      return;
    }

    const elevator = nearestElevator(player.x, player.y);
    if (!elevator) player.elevatorLock = null;
    const elevatorLocked = elevator && player.elevatorLock && Math.hypot(elevator.from.x - player.elevatorLock.x, elevator.from.y - player.elevatorLock.y) < 0.1;
    if (elevator && !elevatorLocked && player.elevatorCooldown <= 0) {
      player.elevatorRide = {
        to: elevator.to,
        startZ: player.z,
        endZ: elevator.to.z,
        progress: 0,
      };
      player.elevatorCooldown = 1.4;
      player.elevatorLock = { x: elevator.to.x, y: elevator.to.y };
      setMessage(elevator.to.z > player.z ? "Elevador subindo para o andar superior." : "Elevador descendo para o piso inferior.");
      return;
    }

    player.targetZ = floorHeightAt(player.x, player.y);
    const lift = Math.max(-1.4 * dt, Math.min(1.4 * dt, player.targetZ - player.z));
    if (Math.abs(lift) > 0.001) {
      player.z += lift;
      updateHud();
      const label = terrainLabelAt(player.x, player.y);
      if (label && bannerTimer <= 0) setMessage(`${label}: andar ${currentFloorName()}.`);
    }
  }

  function updatePlayer(dt) {
    if (!player.alive || player.won) return;

    const turn = (keys.has("ArrowLeft") ? -1 : 0) + (keys.has("ArrowRight") ? 1 : 0);
    player.angle = normalizeAngle(player.angle + turn * dt * 2.25);

    if (player.elevatorRide) {
      updateTraversal(dt);
      return;
    }

    const forward = (keys.has("KeyW") ? 1 : 0) + (keys.has("KeyS") ? -1 : 0);
    const strafe = (keys.has("KeyD") ? 1 : 0) + (keys.has("KeyA") ? -1 : 0);
    const sprint = keys.has("ShiftLeft") || keys.has("ShiftRight");
    const speed = sprint ? 3.15 : 2.15;
    const sin = Math.sin(player.angle);
    const cos = Math.cos(player.angle);
    const dx = (cos * forward + Math.cos(player.angle + Math.PI / 2) * strafe) * speed * dt;
    const dy = (sin * forward + Math.sin(player.angle + Math.PI / 2) * strafe) * speed * dt;
    tryMove(player, dx, dy);
    updateTraversal(dt);

    if ((keys.has("Space") || keys.has("Enter")) && player.fireDelay <= 0) {
      shoot();
    }

    if (player.fireDelay > 0) player.fireDelay -= dt;
    if (player.damageFlash > 0) player.damageFlash -= dt;
  }

  function applyDamage(amount) {
    if (!player.alive || player.won) return;
    const absorbed = Math.min(player.armor, amount * 0.58);
    player.armor -= absorbed;
    player.health -= amount - absorbed;
    player.damageFlash = 0.35;
    shake = Math.max(shake, 7);
    updateHud();
    if (player.health <= 0) {
      player.alive = false;
      setMessage("Voce caiu. Pressione R para reiniciar.");
      overlay.classList.remove("hidden");
      overlay.querySelector("h2").textContent = "Fim de jogo";
      overlay.querySelector("p").textContent = "A arena venceu desta vez. Ajuste a mira e tente de novo.";
      playButton.textContent = "Reiniciar";
    }
  }

  function shoot() {
    const activeWeapon = currentWeapon();
    if (player.ammo < activeWeapon.ammoCost) {
      player.fireDelay = 0.22;
      setMessage(`${activeWeapon.name} sem municao suficiente. Procure caixas amarelas.`);
      return;
    }

    player.ammo -= activeWeapon.ammoCost;
    player.fireDelay = activeWeapon.delay;
    weapon.classList.add("firing");
    window.setTimeout(() => weapon.classList.remove("firing"), Math.min(150, activeWeapon.delay * 420));
    shake = Math.max(shake, activeWeapon.id === "railgun" ? 5 : activeWeapon.id === "shotgun" ? 6 : 3);
    spawnParticle(
      player.x + Math.cos(player.angle) * 0.65,
      player.y + Math.sin(player.angle) * 0.65,
      activeWeapon.particle,
      0.18,
      activeWeapon.id === "shotgun" ? 18 : 10,
    );

    const hits = new Map();
    let reactorHits = 0;
    for (let pellet = 0; pellet < activeWeapon.pellets; pellet += 1) {
      const spread = activeWeapon.pellets === 1 ? 0 : (Math.random() - 0.5) * activeWeapon.cone * 1.8;
      const hit = traceShot(player.angle + spread, activeWeapon);
      if (hit.enemy) {
        hits.set(hit.enemy, (hits.get(hit.enemy) || 0) + hit.damage);
      } else if (hit.reactor) {
        reactorHits += 1;
      }
    }

    hits.forEach((damage, enemy) => damageEnemy(enemy, damage));
    if (!hits.size && reactorHits > 0) {
      damageReactor(activeWeapon.id === "railgun" ? 2 : 1);
    }

    updateHud();
  }

  function traceShot(angle, activeWeapon) {
    let best = null;
    let bestScore = Infinity;
    const targets = enemies.filter((enemy) => enemy.hp > 0);
    targets.forEach((enemy) => {
      const dx = enemy.x - player.x;
      const dy = enemy.y - player.y;
      const dist = Math.hypot(dx, dy);
      if (dist > activeWeapon.range) return;
      const diff = Math.abs(angleDelta(Math.atan2(dy, dx), angle));
      const aimCone = activeWeapon.cone + 0.08 / Math.max(dist, 1);
      if (diff < aimCone && hasLineOfSight(player.x, player.y, enemy.x, enemy.y)) {
        const score = diff * 14 + dist;
        if (score < bestScore) {
          best = enemy;
          bestScore = score;
        }
      }
    });

    const reactorDiff = Math.abs(angleDelta(Math.atan2(reactor.y - player.y, reactor.x - player.x), angle));
    const reactorDist = Math.hypot(reactor.x - player.x, reactor.y - player.y);
    const canHitReactor =
      !reactor.destroyed &&
      reactorDiff < activeWeapon.cone + 0.03 &&
      reactorDist < activeWeapon.range &&
      hasLineOfSight(player.x, player.y, reactor.x, reactor.y);

    if (best) {
      const falloff = activeWeapon.id === "shotgun" ? Math.max(0.35, 1 - bestScore / 10) : 1;
      return {
        enemy: best,
        damage: Math.round((best.kind === "tank" ? activeWeapon.tankDamage : activeWeapon.damage) * falloff),
      };
    }
    return { reactor: canHitReactor };
  }

  function damageEnemy(enemy, damage) {
    enemy.hp -= damage;
    spawnParticle(enemy.x, enemy.y, enemy.kind === "tank" ? "#f35d4f" : "#87f572", 0.35, 18);
    if (enemy.hp <= 0) {
      player.kills += 1;
      player.ammo += enemy.kind === "tank" ? 18 : 6;
      if (player.kills === ENEMY_TOTAL) {
        reactor.shield = false;
        setMessage("Escudos do reator cairam. Destrua o nucleo ao norte.");
      } else {
        setMessage(`Alvo abatido. Restam ${ENEMY_TOTAL - player.kills}.`);
      }
    }
  }

  function damageReactor(amount = 1) {
    if (reactor.shield) {
      setMessage("Reator protegido. Elimine todos os alvos.");
      return;
    }
    reactor.hp -= amount;
    spawnParticle(reactor.x, reactor.y, "#5fd7ff", 0.42, 24);
    if (reactor.hp <= 0) {
      reactor.destroyed = true;
      exit.open = true;
      setMessage("Reator destruido. Volte ao portao verde de extracao.");
      shake = 12;
    } else {
      setMessage(`Nucleo instavel: ${reactor.hp} cargas restantes.`);
    }
  }

  function updateEnemies(dt) {
    if (!player.alive || player.won) return;
    enemies.forEach((enemy) => {
      if (enemy.hp <= 0) return;
      const dx = player.x - enemy.x;
      const dy = player.y - enemy.y;
      const dist = Math.hypot(dx, dy);
      const heightDiff = Math.abs(floorHeightAt(enemy.x, enemy.y) - player.z);
      const seesPlayer = dist < 9.2 && heightDiff < 1.4 && hasLineOfSight(enemy.x, enemy.y, player.x, player.y);

      if (seesPlayer && dist > 0.72) {
        const speed = enemy.speed * (enemy.kind === "runner" && dist < 4 ? 1.5 : 1);
        tryMove(enemy, (dx / dist) * speed * dt, (dy / dist) * speed * dt);
      }

      enemy.cool -= dt;
      if (seesPlayer && dist < (enemy.kind === "gunner" ? 6.6 : 1.05) && enemy.cool <= 0) {
        enemy.cool = enemy.kind === "tank" ? 1.35 : enemy.kind === "gunner" ? 1.05 : 0.72;
        applyDamage(enemy.attack + Math.max(0, 4 - dist));
        spawnParticle(enemy.x, enemy.y, "#f35d4f", 0.18, 8);
      }
    });
  }

  function updatePickups() {
    pickups.forEach((pickup) => {
      if (pickup.taken) return;
      const dist = Math.hypot(player.x - pickup.x, player.y - pickup.y);
      if (dist > 0.55) return;
      pickup.taken = true;
      if (pickup.type === "health") {
        player.health = Math.min(100, player.health + 34);
        setMessage("Kit medico coletado.");
      } else if (pickup.type === "armor") {
        player.armor = Math.min(100, player.armor + 42);
        setMessage("Armadura reforcada.");
      } else {
        player.ammo += 30;
        setMessage("Municao coletada.");
      }
      updateHud();
    });
  }

  function updateExit() {
    if (exit.open && Math.hypot(player.x - exit.x, player.y - exit.y) < 0.75) {
      player.won = true;
      running = false;
      setMessage("Vitoria. Complexo neutralizado.");
      overlay.classList.remove("hidden");
      overlay.querySelector("h2").textContent = "Missao concluida";
      overlay.querySelector("p").textContent = "Voce destruiu o reator, limpou a arena e escapou pelo portao.";
      playButton.textContent = "Jogar novamente";
    }
  }

  function spawnParticle(x, y, color, life, count) {
    for (let i = 0; i < count; i += 1) {
      const angle = Math.random() * TWO_PI;
      const speed = 0.5 + Math.random() * 1.8;
      particles.push({
        x,
        y,
        vx: Math.cos(angle) * speed,
        vy: Math.sin(angle) * speed,
        color,
        life,
        maxLife: life,
      });
    }
  }

  function updateParticles(dt) {
    particles.forEach((particle) => {
      particle.x += particle.vx * dt;
      particle.y += particle.vy * dt;
      particle.life -= dt;
    });
    particles = particles.filter((particle) => particle.life > 0);
  }

  function castRay(angle) {
    const sin = Math.sin(angle);
    const cos = Math.cos(angle);
    let x = player.x;
    let y = player.y;
    let distance = 0;
    let shade = 1;
    let tile = "#";
    let hitX = x;
    let hitY = y;
    let hitFloorZ = floorHeightAt(x, y);

    while (distance < MAX_DEPTH) {
      const previousX = x;
      const previousY = y;
      x += cos * 0.025;
      y += sin * 0.025;
      distance += 0.025;
      tile = tileAt(x, y);
      if (tile === "#") {
        const fx = Math.abs(x - Math.floor(x) - 0.5);
        const fy = Math.abs(y - Math.floor(y) - 0.5);
        shade = fx > fy ? 0.82 : 1;
        hitX = x;
        hitY = y;
        hitFloorZ = floorHeightAt(previousX, previousY);
        break;
      }
    }

    return { distance, shade, tile, x: hitX, y: hitY, floorZ: hitFloorZ };
  }

  function colorWithShade(hex, shade) {
    const num = Number.parseInt(hex.slice(1), 16);
    const r = Math.floor(((num >> 16) & 255) * shade);
    const g = Math.floor(((num >> 8) & 255) * shade);
    const b = Math.floor((num & 255) * shade);
    return `rgb(${r}, ${g}, ${b})`;
  }

  function drawTexturedFloors(horizon) {
    const width = canvas.width;
    const height = canvas.height;
    const startY = Math.max(Math.floor(horizon), 0);
    const playerHeight = 0.52 + player.z * 0.2;

    for (let y = startY; y < height; y += 3) {
      const rowDepth = (playerHeight * height) / Math.max(1, y - horizon + 12);
      if (rowDepth > MAX_DEPTH) continue;

      for (let x = 0; x < width; x += 4) {
        const cameraX = x / width - 0.5;
        const rayAngle = player.angle + cameraX * FOV;
        const distance = rowDepth / Math.max(0.2, Math.cos(rayAngle - player.angle));
        const worldX = player.x + Math.cos(rayAngle) * distance;
        const worldY = player.y + Math.sin(rayAngle) * distance;
        if (isWall(worldX, worldY)) continue;
        const color = texturedFloorColor(worldX, worldY, distance);
        if (!color) continue;
        ctx.fillStyle = color;
        ctx.fillRect(x, y, 4, 3);
      }
    }
  }

  function drawScene() {
    const width = canvas.width;
    const height = canvas.height;
    const horizon = height * (0.48 + player.z * 0.035);
    ctx.fillStyle = "#101519";
    ctx.fillRect(0, 0, width, horizon);

    const ceiling = ctx.createLinearGradient(0, 0, 0, horizon);
    ceiling.addColorStop(0, "#060708");
    ceiling.addColorStop(1, "#192126");
    ctx.fillStyle = ceiling;
    ctx.fillRect(0, 0, width, horizon);

    const floor = ctx.createLinearGradient(0, horizon, 0, height);
    floor.addColorStop(0, "#34302a");
    floor.addColorStop(1, "#0e0f10");
    ctx.fillStyle = floor;
    ctx.fillRect(0, horizon, width, height - horizon);
    drawTexturedFloors(horizon);

    depthBuffer = new Array(width);
    for (let column = 0; column < width; column += 2) {
      const cameraX = column / width - 0.5;
      const rayAngle = player.angle + cameraX * FOV;
      const hit = castRay(rayAngle);
      const corrected = hit.distance * Math.cos(rayAngle - player.angle);
      const wallHeight = Math.min(height * 1.5, height / Math.max(corrected, 0.08));
      const wallZ = hit.floorZ;
      const y = horizon - wallHeight / 2 + (player.z - wallZ) * 28;
      const palette = wallPalette[hit.tile] || wallPalette["#"];
      const fog = Math.max(0.18, 1 - corrected / MAX_DEPTH);
      ctx.fillStyle = colorWithShade(palette[0], fog * hit.shade);
      ctx.fillRect(column, y, 2, wallHeight);
      ctx.fillStyle = colorWithShade(palette[1], fog * hit.shade);
      ctx.fillRect(column, y + wallHeight * 0.78, 2, wallHeight * 0.22);
      depthBuffer[column] = corrected;
      depthBuffer[column + 1] = corrected;
    }
  }

  function projectSprite(x, y, size, z = floorHeightAt(x, y)) {
    const dx = x - player.x;
    const dy = y - player.y;
    const dist = Math.hypot(dx, dy);
    const angle = angleDelta(Math.atan2(dy, dx), player.angle);
    if (Math.abs(angle) > FOV * 0.62 || dist < 0.05) return null;
    const screenX = canvas.width / 2 + Math.tan(angle) / Math.tan(FOV / 2) * canvas.width / 2;
    const scale = canvas.height / dist;
    const verticalOffset = (player.z - z) * scale * 0.18;
    return {
      x: screenX,
      y: canvas.height * (0.5 + player.z * 0.035) + verticalOffset,
      width: scale * size,
      height: scale * size * 1.35,
      dist,
    };
  }

  function drawSprites() {
    const sprites = [];
    enemies.forEach((enemy) => {
      if (enemy.hp > 0) sprites.push({ ...enemy, spriteType: "enemy", size: enemy.kind === "tank" ? 0.82 : 0.55 });
    });
    pickups.forEach((pickup) => {
      if (!pickup.taken) sprites.push({ ...pickup, spriteType: "pickup", size: 0.34 });
    });
    if (!reactor.destroyed) sprites.push({ ...reactor, spriteType: "reactor", size: 0.86 });
    if (exit.open) sprites.push({ ...exit, spriteType: "exit", size: 0.72 });
    particles.forEach((particle) => sprites.push({ ...particle, spriteType: "particle", size: 0.12 }));

    sprites
      .map((sprite) => ({ sprite, projection: projectSprite(sprite.x, sprite.y, sprite.size, sprite.z ?? floorHeightAt(sprite.x, sprite.y)) }))
      .filter((entry) => entry.projection)
      .sort((a, b) => b.projection.dist - a.projection.dist)
      .forEach(({ sprite, projection }) => {
        const left = Math.floor(projection.x - projection.width / 2);
        const right = Math.floor(projection.x + projection.width / 2);
        let visible = false;
        for (let x = left; x <= right; x += 8) {
          if (x >= 0 && x < canvas.width && projection.dist < (depthBuffer[x] || MAX_DEPTH)) {
            visible = true;
            break;
          }
        }
        if (!visible) return;

        if (sprite.spriteType === "enemy") drawEnemySprite(sprite, projection);
        if (sprite.spriteType === "pickup") drawPickupSprite(sprite, projection);
        if (sprite.spriteType === "reactor") drawReactorSprite(sprite, projection);
        if (sprite.spriteType === "exit") drawExitSprite(projection);
        if (sprite.spriteType === "particle") drawParticleSprite(sprite, projection);
      });
  }

  function drawEnemySprite(enemy, p) {
    const x = p.x - p.width / 2;
    const y = p.y - p.height / 2;
    const body = enemy.kind === "tank" ? "#913a32" : enemy.kind === "runner" ? "#81c35f" : "#b4573d";
    const trim = enemy.kind === "gunner" ? "#f4b648" : "#24272b";
    ctx.fillStyle = "rgba(0,0,0,0.35)";
    ctx.beginPath();
    ctx.ellipse(p.x, y + p.height, p.width * 0.42, p.height * 0.08, 0, 0, TWO_PI);
    ctx.fill();
    ctx.fillStyle = body;
    ctx.fillRect(x + p.width * 0.22, y + p.height * 0.18, p.width * 0.56, p.height * 0.66);
    ctx.fillStyle = trim;
    ctx.fillRect(x + p.width * 0.34, y + p.height * 0.08, p.width * 0.32, p.height * 0.18);
    ctx.fillStyle = "#1b0c0c";
    ctx.fillRect(x + p.width * 0.38, y + p.height * 0.16, p.width * 0.08, p.height * 0.05);
    ctx.fillRect(x + p.width * 0.54, y + p.height * 0.16, p.width * 0.08, p.height * 0.05);
    ctx.fillStyle = "#15171a";
    ctx.fillRect(x + p.width * 0.08, y + p.height * 0.42, p.width * 0.22, p.height * 0.14);
    ctx.fillRect(x + p.width * 0.70, y + p.height * 0.42, p.width * 0.22, p.height * 0.14);
    const hpRatio = Math.max(0, enemy.hp) / (enemy.kind === "tank" ? 120 : enemy.kind === "gunner" ? 64 : 48);
    ctx.fillStyle = "#111";
    ctx.fillRect(x + p.width * 0.16, y - 8, p.width * 0.68, 4);
    ctx.fillStyle = "#7cf27a";
    ctx.fillRect(x + p.width * 0.16, y - 8, p.width * 0.68 * hpRatio, 4);
  }

  function drawPickupSprite(pickup, p) {
    const color = pickup.type === "health" ? "#f35d4f" : pickup.type === "armor" ? "#5fd7ff" : "#f4b648";
    ctx.fillStyle = color;
    ctx.fillRect(p.x - p.width / 2, p.y - p.height * 0.18, p.width, p.height * 0.36);
    ctx.fillStyle = "rgba(255,255,255,0.65)";
    ctx.fillRect(p.x - p.width * 0.08, p.y - p.height * 0.15, p.width * 0.16, p.height * 0.3);
  }

  function drawReactorSprite(core, p) {
    const x = p.x - p.width / 2;
    const y = p.y - p.height / 2;
    ctx.fillStyle = core.shield ? "rgba(95, 215, 255, 0.22)" : "rgba(244, 182, 72, 0.18)";
    ctx.beginPath();
    ctx.arc(p.x, p.y, p.width * 0.56, 0, TWO_PI);
    ctx.fill();
    ctx.fillStyle = "#26313a";
    ctx.fillRect(x + p.width * 0.22, y + p.height * 0.14, p.width * 0.56, p.height * 0.72);
    ctx.fillStyle = core.shield ? "#5fd7ff" : "#f4b648";
    ctx.fillRect(x + p.width * 0.36, y + p.height * 0.24, p.width * 0.28, p.height * 0.52);
    ctx.fillStyle = "#0b1014";
    ctx.fillRect(x + p.width * 0.1, y + p.height * 0.78, p.width * 0.8, p.height * 0.08);
  }

  function drawExitSprite(p) {
    ctx.fillStyle = "rgba(124,242,122,0.24)";
    ctx.fillRect(p.x - p.width / 2, p.y - p.height / 2, p.width, p.height);
    ctx.strokeStyle = "#7cf27a";
    ctx.lineWidth = 4;
    ctx.strokeRect(p.x - p.width / 2, p.y - p.height / 2, p.width, p.height);
  }

  function drawParticleSprite(particle, p) {
    ctx.globalAlpha = Math.max(0, particle.life / particle.maxLife);
    ctx.fillStyle = particle.color;
    ctx.beginPath();
    ctx.arc(p.x, p.y, Math.max(2, p.width), 0, TWO_PI);
    ctx.fill();
    ctx.globalAlpha = 1;
  }

  function drawMinimap() {
    const scale = 6;
    const pad = 12;
    const top = canvas.height - level.length * scale - pad;
    ctx.save();
    ctx.globalAlpha = 0.82;
    ctx.fillStyle = "rgba(5,7,8,0.72)";
    ctx.fillRect(pad - 6, top - 6, level[0].length * scale + 12, level.length * scale + 12);
    for (let y = 0; y < level.length; y += 1) {
      for (let x = 0; x < level[y].length; x += 1) {
        ctx.fillStyle = tileColors[level[y][x]] || "#151a1d";
        ctx.fillRect(pad + x * scale, top + y * scale, scale - 1, scale - 1);
      }
    }
    ctx.strokeStyle = "rgba(255,255,255,0.34)";
    heightZones.forEach((zone) => {
      ctx.strokeRect(pad + zone.x1 * scale, top + zone.y1 * scale, (zone.x2 - zone.x1) * scale, (zone.y2 - zone.y1) * scale);
    });
    ctx.fillStyle = exit.open ? "#7cf27a" : "#30412e";
    ctx.fillRect(pad + exit.x * scale - 2, top + exit.y * scale - 2, 4, 4);
    ctx.fillStyle = "#f35d4f";
    enemies.forEach((enemy) => {
      if (enemy.hp > 0) ctx.fillRect(pad + enemy.x * scale - 1, top + enemy.y * scale - 1, 3, 3);
    });
    ctx.fillStyle = "#f3f7f4";
    ctx.beginPath();
    ctx.arc(pad + player.x * scale, top + player.y * scale, 2.5, 0, TWO_PI);
    ctx.fill();
    ctx.strokeStyle = "#f3f7f4";
    ctx.beginPath();
    ctx.moveTo(pad + player.x * scale, top + player.y * scale);
    ctx.lineTo(pad + (player.x + Math.cos(player.angle) * 0.8) * scale, top + (player.y + Math.sin(player.angle) * 0.8) * scale);
    ctx.stroke();
    ctx.restore();
  }

  function drawOverlayEffects() {
    if (player.damageFlash > 0) {
      ctx.fillStyle = `rgba(243, 93, 79, ${player.damageFlash * 0.45})`;
      ctx.fillRect(0, 0, canvas.width, canvas.height);
    }
    if (bannerTimer > 0) {
      message.style.opacity = "1";
    } else {
      message.style.opacity = "0";
    }
  }

  function render() {
    ctx.save();
    if (shake > 0) {
      ctx.translate((Math.random() - 0.5) * shake, (Math.random() - 0.5) * shake);
      shake *= 0.9;
      if (shake < 0.2) shake = 0;
    }
    drawScene();
    drawSprites();
    drawMinimap();
    drawOverlayEffects();
    ctx.restore();
  }

  function tick(time) {
    const dt = Math.min(0.05, (time - lastTime) / 1000 || 0);
    lastTime = time;
    if (running) {
      updatePlayer(dt);
      updateEnemies(dt);
      updatePickups();
      updateExit();
      updateParticles(dt);
      if (bannerTimer > 0) bannerTimer -= dt;
    }
    render();
    requestAnimationFrame(tick);
  }

  window.addEventListener("keydown", (event) => {
    keys.add(event.code);
    if (["Space", "ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.code)) {
      event.preventDefault();
    }
    if (event.code === "KeyR") {
      overlay.classList.add("hidden");
      running = true;
      resetGame();
    }
    if (event.code === "Digit1") selectWeapon(0);
    if (event.code === "Digit2") selectWeapon(1);
    if (event.code === "Digit3") selectWeapon(2);
    if (event.code === "KeyQ") cycleWeapon();
  });

  window.addEventListener("keyup", (event) => {
    keys.delete(event.code);
  });

  window.addEventListener("mousemove", (event) => {
    if (document.pointerLockElement === canvas && player.alive && !player.won) {
      player.angle = normalizeAngle(player.angle + event.movementX * 0.0022);
    }
  });

  canvas.addEventListener("click", () => {
    if (!running || !player.alive || player.won) {
      startGame();
      return;
    }
    if (document.pointerLockElement !== canvas) lockPointer();
    if (player.fireDelay <= 0) shoot();
  });

  playButton.addEventListener("click", startGame);
  startButton.addEventListener("click", startGame);
  restartButton.addEventListener("click", () => {
    overlay.classList.add("hidden");
    running = true;
    resetGame();
  });

  resetGame();
  requestAnimationFrame(tick);
})();
