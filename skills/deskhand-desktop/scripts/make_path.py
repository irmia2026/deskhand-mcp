"""Generate bounded image-space drag_path arguments; never sends desktop input."""

import argparse
import json
import math


def make_path(kind, image_id, image_size, center, radius, start, end, segments):
    if not image_id:
        raise ValueError("image_id must come from the current observation")
    if len(image_size) != 2 or min(image_size) <= 0:
        raise ValueError("image_size must contain positive width and height")
    if not 2 <= segments <= 1023:
        raise ValueError("segments must be in 2..1023")
    if not all(math.isfinite(v) for v in (*center, *radius, start, end)):
        raise ValueError("geometry must be finite")
    if min(radius) <= 0:
        raise ValueError("radius must be positive")
    if kind == "circle":
        start, end = 0, 360
    elif kind != "arc":
        raise ValueError("kind must be circle or arc")
    if not 0 < abs(end - start) <= 360:
        raise ValueError("arc span must be greater than 0 and at most 360 degrees")
    points = []
    for i in range(segments + 1):
        angle = math.radians(start + (end - start) * i / segments)
        point = [
            round(center[0] + radius[0] * math.cos(angle)),
            round(center[1] + radius[1] * math.sin(angle)),
        ]
        if not 0 <= point[0] < image_size[0] or not 0 <= point[1] < image_size[1]:
            raise ValueError(
                "path exceeds image bounds; correct geometry before drawing"
            )
        if not points or point != points[-1]:
            points.append(point)
    if len(points) < 2 or len({tuple(p) for p in points}) < 2:
        raise ValueError("path is too small to draw")
    return {"image_id": image_id, "points": points, "step_ms": 12}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", choices=("circle", "arc"), required=True)
    parser.add_argument("--image-id", required=True)
    parser.add_argument("--image-size", nargs=2, type=int, required=True)
    parser.add_argument("--center", nargs=2, type=float, required=True)
    parser.add_argument("--radius", nargs=2, type=float, required=True)
    parser.add_argument("--start", type=float, default=0)
    parser.add_argument("--end", type=float, default=360)
    parser.add_argument("--segments", type=int, default=64)
    args = parser.parse_args()
    try:
        result = make_path(
            args.kind,
            args.image_id,
            args.image_size,
            args.center,
            args.radius,
            args.start,
            args.end,
            args.segments,
        )
    except ValueError as error:
        parser.error(str(error))
    print(json.dumps(result, separators=(",", ":")))


if __name__ == "__main__":
    main()
