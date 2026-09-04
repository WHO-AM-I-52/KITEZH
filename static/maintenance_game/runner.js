// ==================================================================
//  GAME: RUNNER
// ==================================================================
GAMES.runner = function() {
  var player = {
    width: 30,
    height: 30,
    x: 50,
    y: 0,
    velocityY: 0,
    grounded: true,
    color: '#4db8ff',
  };

  var gravity = 0.5;
  var defaultJumpForce = -12;
  var jumpForce = defaultJumpForce;
  var groundLevel = H - 30;

  var obstacles = [];
  var powerUps = [];

  var obstacleSpeed = 5;
  var obstacleInterval = 1200;
  var powerUpInterval = 5000;
  var lastObstacleTime = Date.now();
  var lastPowerUpTime = Date.now();

  var score = 0;
  var highScoreKey = 'kitezh_runner_high_score';
  var highScore = Number(localStorage.getItem(highScoreKey)) || 0;

  var scoreMultiplier = 1;
  var shieldActive = false;
  var timeSlowed = false;
  var gameOver = false;
  var backgroundX = 0;
  var timers = [];

  player.y = groundLevel - player.height;

  function isCollision(a, b) {
    return (
      a.x < b.x + b.width &&
      a.x + a.width > b.x &&
      a.y < b.y + b.height &&
      a.y + a.height > b.y
    );
  }

  function clearTimers() {
    timers.forEach(function(timerId) {
      clearTimeout(timerId);
    });
    timers = [];
  }

  function addTimer(callback, duration) {
    var timerId = setTimeout(callback, duration);
    timers.push(timerId);
  }

  function jump() {
    if (!player.grounded || gameOver) return;

    player.grounded = false;
    player.velocityY = jumpForce;
  }

  function restart() {
    clearTimers();

    groundLevel = H - 30;
    player.x = 50;
    player.y = groundLevel - player.height;
    player.velocityY = 0;
    player.grounded = true;

    obstacles = [];
    powerUps = [];
    obstacleSpeed = 5;
    obstacleInterval = 1200;
    lastObstacleTime = Date.now();
    lastPowerUpTime = Date.now();

    score = 0;
    scoreMultiplier = 1;
    shieldActive = false;
    timeSlowed = false;
    jumpForce = defaultJumpForce;
    gameOver = false;

    loop();
  }

  function handleAction(event) {
    if (event) {
      event.preventDefault();
    }

    if (gameOver) {
      restart();
      return;
    }

    jump();
  }

  function onKeyDown(event) {
    if (event.code !== 'Space') return;
    handleAction(event);
  }

  function onPointerDown(event) {
    if (
      event.target.closest('#topbar') ||
      event.target.closest('#modal-backdrop') ||
      event.target.closest('#controls-popup')
    ) {
      return;
    }

    handleAction(event);
  }

  function createObstacle() {
    obstacles.push({
      width: 30,
      height: 30,
      x: W,
      y: groundLevel - 30,
      color: '#ff4d4d',
    });
  }

  function createPowerUp() {
    var types = ['scoreMultiplier', 'shield', 'highJump', 'slowTime'];
    var type = types[Math.floor(Math.random() * types.length)];

    var colors = {
      scoreMultiplier: '#ffd700',
      shield: '#00bfff',
      highJump: '#32cd32',
      slowTime: '#8a2be2',
    };

    powerUps.push({
      width: 20,
      height: 20,
      x: W,
      y: groundLevel - 20 - Math.random() * 80 - 40,
      type: type,
      color: colors[type],
      duration: 5000,
    });
  }

  function activatePowerUp(powerUp) {
    if (powerUp.type === 'scoreMultiplier') {
      scoreMultiplier = 2;
      addTimer(function() {
        scoreMultiplier = 1;
      }, powerUp.duration);
    }

    if (powerUp.type === 'shield') {
      shieldActive = true;
      addTimer(function() {
        shieldActive = false;
      }, powerUp.duration);
    }

    if (powerUp.type === 'highJump') {
      jumpForce = -18;
      addTimer(function() {
        jumpForce = defaultJumpForce;
      }, powerUp.duration);
    }

    if (powerUp.type === 'slowTime') {
      timeSlowed = true;
      addTimer(function() {
        timeSlowed = false;
      }, powerUp.duration);
    }
  }

  function endGame() {
    if (gameOver) return;

    gameOver = true;

    if (score > highScore) {
      highScore = score;
      localStorage.setItem(highScoreKey, String(highScore));
    }
  }

  function update() {
    groundLevel = H - 30;

    if (!player.grounded) {
      player.velocityY += gravity;
    }

    player.y += player.velocityY;

    if (player.y >= groundLevel - player.height) {
      player.y = groundLevel - player.height;
      player.velocityY = 0;
      player.grounded = true;
    }

    var speed = timeSlowed ? obstacleSpeed / 2 : obstacleSpeed;

    obstacles.forEach(function(obstacle) {
      obstacle.x -= speed;
    });

    powerUps.forEach(function(powerUp) {
      powerUp.x -= speed;
    });

    var now = Date.now();
    var currentObstacleInterval = timeSlowed
      ? obstacleInterval * 2
      : obstacleInterval;

    var currentPowerUpInterval = timeSlowed
      ? powerUpInterval * 2
      : powerUpInterval;

    if (now - lastObstacleTime > currentObstacleInterval) {
      createObstacle();
      lastObstacleTime = now;
    }

    if (now - lastPowerUpTime > currentPowerUpInterval) {
      createPowerUp();
      lastPowerUpTime = now;
    }

    for (var obstacleIndex = obstacles.length - 1; obstacleIndex >= 0; obstacleIndex--) {
      var obstacle = obstacles[obstacleIndex];

      if (!isCollision(player, obstacle)) continue;

      if (shieldActive) {
        shieldActive = false;
        obstacles.splice(obstacleIndex, 1);
      } else {
        endGame();
        return;
      }
    }

    for (var powerUpIndex = powerUps.length - 1; powerUpIndex >= 0; powerUpIndex--) {
      var powerUp = powerUps[powerUpIndex];

      if (!isCollision(player, powerUp)) continue;

      powerUps.splice(powerUpIndex, 1);
      activatePowerUp(powerUp);
    }

    obstacles = obstacles.filter(function(obstacle) {
      return obstacle.x + obstacle.width > 0;
    });

    powerUps = powerUps.filter(function(powerUp) {
      return powerUp.x + powerUp.width > 0;
    });

    obstacleSpeed += timeSlowed ? 0.0005 : 0.001;

    if (obstacleInterval > 500) {
      obstacleInterval -= timeSlowed ? 0.05 : 0.1;
    }

    score += scoreMultiplier;
  }

  function drawBackground() {
    ctx.fillStyle = '#87ceeb';
    ctx.fillRect(0, 0, W, groundLevel);

    ctx.fillStyle = '#6b5e54';
    ctx.fillRect(0, groundLevel, W, H - groundLevel);

    ctx.fillStyle = '#d9e3eb';

    for (var i = 0; i < 50; i++) {
      var starX = (i * 50 + backgroundX) % W;

      ctx.beginPath();
      ctx.arc(starX, (i * 20) % groundLevel, 1, 0, Math.PI * 2);
      ctx.fill();
    }

    backgroundX -= 1;
  }

  function drawUI() {
    ctx.font = '20px Arial';
    ctx.fillStyle = '#ffffff';
    ctx.textAlign = 'left';
    ctx.fillText('Очки: ' + score, 12, 28);

    ctx.textAlign = 'right';
    ctx.fillText('Рекорд: ' + highScore, W - 12, 28);

    ctx.textAlign = 'center';

    if (scoreMultiplier > 1) {
      ctx.fillStyle = '#ffd700';
      ctx.fillText('Множитель ×' + scoreMultiplier, W / 2, 28);
    }

    if (shieldActive) {
      ctx.fillStyle = '#00bfff';
      ctx.fillText('Щит активен', W / 2, 54);
    }

    if (timeSlowed) {
      ctx.fillStyle = '#8a2be2';
      ctx.fillText('Время замедлено', W / 2, 80);
    }
  }

  function drawGameOver() {
    ctx.fillStyle = 'rgba(0, 0, 0, 0.48)';
    ctx.fillRect(0, 0, W, H);

    ctx.fillStyle = '#ffffff';
    ctx.font = '40px Arial';
    ctx.textAlign = 'center';
    ctx.fillText('Игра окончена', W / 2, H / 2 - 40);

    ctx.font = '24px Arial';
    ctx.fillText('Ваш результат: ' + score, W / 2, H / 2);

    ctx.fillText(
      'Пробел или клик — новая попытка',
      W / 2,
      H / 2 + 42
    );
  }

  function draw() {
    ctx.clearRect(0, 0, W, H);

    drawBackground();

    ctx.fillStyle = player.color;
    ctx.fillRect(player.x, player.y, player.width, player.height);

    if (shieldActive) {
      ctx.strokeStyle = 'rgba(255, 255, 255, 0.75)';
      ctx.lineWidth = 4;
      ctx.beginPath();
      ctx.arc(
        player.x + player.width / 2,
        player.y + player.height / 2,
        player.width,
        0,
        Math.PI * 2
      );
      ctx.stroke();
    }

    obstacles.forEach(function(obstacle) {
      ctx.fillStyle = obstacle.color;
      ctx.fillRect(obstacle.x, obstacle.y, obstacle.width, obstacle.height);
    });

    powerUps.forEach(function(powerUp) {
      ctx.fillStyle = powerUp.color;
      ctx.fillRect(powerUp.x, powerUp.y, powerUp.width, powerUp.height);
    });

    drawUI();

    if (gameOver) {
      drawGameOver();
    }
  }

  function loop() {
    if (gameOver) {
      draw();
      currentRAF = null;
      return;
    }

    update();
    draw();

    currentRAF = requestAnimationFrame(loop);
  }

  window.addEventListener('keydown', onKeyDown);
  window.addEventListener('pointerdown', onPointerDown);

  currentCleanup = function() {
    clearTimers();
    window.removeEventListener('keydown', onKeyDown);
    window.removeEventListener('pointerdown', onPointerDown);
  };

  loop();
};
