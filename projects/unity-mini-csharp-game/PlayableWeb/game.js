(() => {
  const canvas = document.getElementById("game");
  const ctx = canvas.getContext("2d");
  const hudScore = document.getElementById("score");
  const hudLives = document.getElementById("lives");
  const hudState = document.getElementById("state");
  const btnRestart = document.getElementById("restart");

  const keys = new Set();
  const world = {
    width: canvas.width,
    height: canvas.height,
    gravity: 0.65,
    friction: 0.82,
    cameraX: 0,
    levelWidth: 2600,
  };

  const playerStart = { x: 80, y: 330 };

  const player = {
    x: playerStart.x,
    y: playerStart.y,
    width: 32,
    height: 42,
    vx: 0,
    vy: 0,
    speed: 0.72,
    maxSpeed: 5.4,
    jumpPower: -13.2,
    grounded: false,
    invulnerable: 0,
    lives: 3,
    score: 0,
    won: false,
    dead: false,
  };

  const platforms = [
    { x: 0, y: 520, width: 620, height: 60 },
    { x: 720, y: 500, width: 420, height: 80 },
    { x: 1250, y: 470, width: 360, height: 110 },
    { x: 1700, y: 520, width: 390, height: 60 },
    { x: 2200, y: 490, width: 390, height: 90 },
    { x: 320, y: 405, width: 150, height: 20 },
    { x: 850, y: 380, width: 150, height: 20 },
    { x: 1420, y: 340, width: 170, height: 20 },
    { x: 1900, y: 390, width: 160, height: 20 },
  ];

  const coins = [
    { x: 360, y: 360, taken: false },
    { x: 440, y: 360, taken: false },
    { x: 900, y: 335, taken: false },
    { x: 980, y: 335, taken: false },
    { x: 1320, y: 420, taken: false },
    { x: 1500, y: 295, taken: false },
    { x: 1940, y: 345, taken: false },
    { x: 2020, y: 345, taken: false },
    { x: 2300, y: 445, taken: false },
    { x: 2440, y: 445, taken: false },
  ];

  const enemies = [
    { x: 760, y: 462, width: 34, height: 38, vx: 1.6, min: 740, max: 1090 },
    { x: 1310, y: 432, width: 34, height: 38, vx: 1.8, min: 1260, max: 1570 },
    { x: 1740, y: 482, width: 34, height: 38, vx: 1.5, min: 1710, max: 2050 },
    { x: 2250, y: 452, width: 34, height: 38, vx: 2.0, min: 2220, max: 2530 },
  ];

  const goal = { x: 2520, y: 390, width: 42, height: 100 };

  function resetGame() {
    player.x = playerStart.x;
    player.y = playerStart.y;
    player.vx = 0;
    player.vy = 0;
    player.grounded = false;
    player.invulnerable = 0;
    player.lives = 3;
    player.score = 0;
    player.won = false;
    player.dead = false;
    world.cameraX = 0;

    coins.forEach((coin) => {
      coin.taken = false;
    });

    hudState.textContent = "Pegue moedas, evite inimigos e alcance a bandeira.";
    updateHud();
  }

  function respawnPlayer() {
    player.x = playerStart.x;
    player.y = playerStart.y;
    player.vx = 0;
    player.vy = 0;
    player.invulnerable = 100;
  }

  function updateHud() {
    hudScore.textContent = String(player.score);
    hudLives.textContent = String(player.lives);
  }

  function rectsOverlap(a, b) {
    return (
      a.x < b.x + b.width &&
      a.x + a.width > b.x &&
      a.y < b.y + b.height &&
      a.y + a.height > b.y
    );
  }

  function coinOverlap(coin) {
    const centerX = coin.x + 10;
    const centerY = coin.y + 10;
    return (
      centerX > player.x &&
      centerX < player.x + player.width &&
      centerY > player.y &&
      centerY < player.y + player.height
    );
  }

  function handleInput() {
    if (player.dead || player.won) return;

    const left = keys.has("ArrowLeft") || keys.has("KeyA");
    const right = keys.has("ArrowRight") || keys.has("KeyD");
    const jump = keys.has("Space") || keys.has("ArrowUp") || keys.has("KeyW");

    if (left) player.vx -= player.speed;
    if (right) player.vx += player.speed;

    player.vx = Math.max(-player.maxSpeed, Math.min(player.maxSpeed, player.vx));

    if (jump && player.grounded) {
      player.vy = player.jumpPower;
      player.grounded = false;
    }
  }
  

  function movePlayer() {
    player.vx *= world.friction;
    player.vy += world.gravity;

    player.x += player.vx;
    player.x = Math.max(0, Math.min(world.levelWidth - player.width, player.x));

    for (const platform of platforms) {
      if (!rectsOverlap(player, platform)) continue;

      if (player.vx > 0) player.x = platform.x - player.width;
      if (player.vx < 0) player.x = platform.x + platform.width;
      player.vx = 0;
    }

    player.y += player.vy;
    player.grounded = false;

    for (const platform of platforms) {
      if (!rectsOverlap(player, platform)) continue;

      if (player.vy > 0) {
        player.y = platform.y - player.height;
        player.vy = 0;
        player.grounded = true;
      } else if (player.vy < 0) {
        player.y = platform.y + platform.height;
        player.vy = 0;
      }
    }

    if (player.y > world.height + 160) {
      damagePlayer();
    }

    world.cameraX = Math.max(
      0,
      Math.min(world.levelWidth - world.width, player.x - world.width * 0.38)
    );
  }

  function updateEnemies() {
    enemies.forEach((enemy) => {
      enemy.x += enemy.vx;
      if (enemy.x < enemy.min || enemy.x > enemy.max) {
        enemy.vx *= -1;
        enemy.x += enemy.vx;
      }

      if (!player.dead && !player.won && rectsOverlap(player, enemy)) {
        if (player.vy > 2 && player.y + player.height - enemy.y < 18) {
          enemy.x = enemy.min - 200;
          player.vy = -8.5;
          player.score += 50;
          updateHud();
        } else {
          damagePlayer();
        }
      }
    });
  }

  function damagePlayer() {
    if (player.invulnerable > 0 || player.dead || player.won) return;

    player.lives -= 1;
    updateHud();

    if (player.lives <= 0) {
      player.dead = true;
      hudState.textContent = "Fim de jogo! Clique em reiniciar para tentar de novo.";
      return;
    }

    hudState.textContent = "Você levou dano! Continue tentando.";
    respawnPlayer();
  }

  function collectCoins() {
    coins.forEach((coin) => {
      if (!coin.taken && coinOverlap(coin)) {
        coin.taken = true;
        player.score += 10;
        updateHud();
      }
    });
  }

  function checkGoal() {
    if (!player.won && rectsOverlap(player, goal)) {
      player.won = true;
      player.score += 100;
      hudState.textContent = "Vitória! Você completou o jogo jogável.";
      updateHud();
    }
  }

  function update() {
    handleInput();

    if (!player.dead && !player.won) {
      movePlayer();
      updateEnemies();
      collectCoins();
      checkGoal();
      if (player.invulnerable > 0) player.invulnerable -= 1;
    }
  }

  function drawBackground() {
    const gradient = ctx.createLinearGradient(0, 0, 0, world.height);
    gradient.addColorStop(0, "#7dd3fc");
    gradient.addColorStop(0.58, "#bfdbfe");
    gradient.addColorStop(1, "#d9f99d");
    ctx.fillStyle = gradient;
    ctx.fillRect(0, 0, world.width, world.height);

    ctx.fillStyle = "rgba(255,255,255,0.75)";
    for (let i = 0; i < 9; i += 1) {
      const cloudX = (i * 330 - world.cameraX * 0.35) % (world.width + 180) - 90;
      const cloudY = 70 + (i % 3) * 48;
      ctx.beginPath();
      ctx.arc(cloudX, cloudY, 28, 0, Math.PI * 2);
      ctx.arc(cloudX + 28, cloudY - 12, 34, 0, Math.PI * 2);
      ctx.arc(cloudX + 62, cloudY, 25, 0, Math.PI * 2);
      ctx.fill();
    }
  }

  function drawPlatforms() {
    platforms.forEach((platform) => {
      const x = platform.x - world.cameraX;
      ctx.fillStyle = "#166534";
      ctx.fillRect(x, platform.y, platform.width, platform.height);
      ctx.fillStyle = "#22c55e";
      ctx.fillRect(x, platform.y, platform.width, 12);
      ctx.fillStyle = "rgba(0,0,0,0.15)";
      ctx.fillRect(x, platform.y + platform.height - 8, platform.width, 8);
    });
  }

  function drawCoins() {
    coins.forEach((coin) => {
      if (coin.taken) return;
      const x = coin.x - world.cameraX;
      ctx.fillStyle = "#facc15";
      ctx.beginPath();
      ctx.arc(x + 10, coin.y + 10, 10, 0, Math.PI * 2);
      ctx.fill();
      ctx.strokeStyle = "#ca8a04";
      ctx.lineWidth = 3;
      ctx.stroke();
    });
  }

  function drawEnemies() {
    enemies.forEach((enemy) => {
      if (enemy.x < -100) return;
      const x = enemy.x - world.cameraX;
      ctx.fillStyle = "#dc2626";
      ctx.fillRect(x, enemy.y, enemy.width, enemy.height);
      ctx.fillStyle = "#7f1d1d";
      ctx.fillRect(x + 6, enemy.y + 8, 7, 7);
      ctx.fillRect(x + 21, enemy.y + 8, 7, 7);
      ctx.fillStyle = "#111827";
      ctx.fillRect(x + 4, enemy.y + enemy.height, 9, 6);
      ctx.fillRect(x + 21, enemy.y + enemy.height, 9, 6);
    });
  }

  function drawGoal() {
    const x = goal.x - world.cameraX;
    ctx.fillStyle = "#713f12";
    ctx.fillRect(x, goal.y, 7, goal.height);
    ctx.fillStyle = "#2563eb";
    ctx.beginPath();
    ctx.moveTo(x + 7, goal.y + 8);
    ctx.lineTo(x + 58, goal.y + 28);
    ctx.lineTo(x + 7, goal.y + 50);
    ctx.closePath();
    ctx.fill();
  }

  function drawPlayer() {
    const x = player.x - world.cameraX;
    const blink = player.invulnerable > 0 && Math.floor(player.invulnerable / 8) % 2 === 0;
    if (blink) return;

    ctx.fillStyle = "#1d4ed8";
    ctx.fillRect(x, player.y + 10, player.width, player.height - 10);
    ctx.fillStyle = "#f59e0b";
    ctx.fillRect(x + 5, player.y, player.width - 10, 18);
    ctx.fillStyle = "#111827";
    ctx.fillRect(x + 8, player.y + 7, 5, 5);
    ctx.fillRect(x + 20, player.y + 7, 5, 5);
    ctx.fillStyle = "#0f172a";
    ctx.fillRect(x + 4, player.y + player.height, 10, 6);
    ctx.fillRect(x + 19, player.y + player.height, 10, 6);
  }

  function drawOverlay() {
    if (!player.dead && !player.won) return;

    ctx.fillStyle = "rgba(15,23,42,0.72)";
    ctx.fillRect(0, 0, world.width, world.height);
    ctx.fillStyle = "#ffffff";
    ctx.font = "bold 44px Arial";
    ctx.textAlign = "center";
    ctx.fillText(player.won ? "Vitória!" : "Fim de jogo", world.width / 2, 250);
    ctx.font = "22px Arial";
    ctx.fillText("Clique em Reiniciar para jogar novamente", world.width / 2, 295);
    ctx.textAlign = "left";
  }

  function draw() {
    drawBackground();
    drawPlatforms();
    drawCoins();
    drawEnemies();
    drawGoal();
    drawPlayer();
    drawOverlay();
  }

  function loop() {
    update();
    draw();
    requestAnimationFrame(loop);
  }

  window.addEventListener("keydown", (event) => {
    if (["ArrowLeft", "ArrowRight", "ArrowUp", "Space"].includes(event.code)) {
      event.preventDefault();
    }
    keys.add(event.code);
  });

  window.addEventListener("keyup", (event) => {
    keys.delete(event.code);
  });

  btnRestart.addEventListener("click", resetGame);

  resetGame();
  loop();
})();
