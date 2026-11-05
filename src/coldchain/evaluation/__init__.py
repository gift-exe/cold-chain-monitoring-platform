"""Evaluation of the prototype (Section 3.2.7, Table 3.10; iteration I8). Results go to runs/eval/."""
import math


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    """Wilson score interval for a proportion k/n (95% by default)."""
    if n == 0:
        return None
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return round(centre - half, 4), round(centre + half, 4)
