"""Wall-clock pacing without modifying firmware CPU/timer frequency."""
import math
import time


class SimulationClock:
    def __init__(self, speed=1.0, now=time.perf_counter):
        self.now = now
        self.speed = speed
        self.anchor_cycles = 0
        self.anchor_time = self.now()
        self.elapsed_wall = 0.0
        self.elapsed_cycles = 0
        self.active = False

    def resume(self, cycles):
        if not self.active:
            self.anchor_cycles = cycles
            self.anchor_time = self.now()
            self.active = True

    def pause(self, cycles):
        if self.active:
            self.elapsed_wall += max(0.0, self.now() - self.anchor_time)
            self.elapsed_cycles += max(0, cycles - self.anchor_cycles)
            self.active = False

    def set_speed(self, speed, cycles):
        speed = float(speed)
        if not math.isfinite(speed) or speed < 0 or speed > 4:
            raise ValueError("Velocidade deve estar entre 0 (máximo) e 4×.")
        running = self.active
        self.pause(cycles)
        self.speed = speed
        if running:
            self.resume(cycles)

    def due(self, cycles, limit=500000):
        if not self.active:
            return 0
        if self.speed == 0:
            return limit
        target = self.anchor_cycles + int(max(0.0, self.now() - self.anchor_time) * 16_000_000 * self.speed)
        return min(limit, max(0, target - cycles))

    def metrics(self, cycles):
        wall = self.elapsed_wall
        advanced = self.elapsed_cycles
        if self.active:
            wall += max(0.0, self.now() - self.anchor_time)
            advanced += max(0, cycles - self.anchor_cycles)
        return {"wall_ms": wall * 1000, "speed_ratio": advanced / (16_000_000 * wall) if wall else 0.0,
                "requested_speed": self.speed}
