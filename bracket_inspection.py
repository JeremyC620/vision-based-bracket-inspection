"""Prototype inspection of printed mounting plates from phone photographs.

Run from Terminal:
    python bracket_inspection_v5_candidate.py

V5 compares the shape of the three-hole triangle using ratios of pixel-to-pixel
hole spacings. Unlike fixed image coordinates, these ratios tolerate rotation,
translation, and camera distance changes. They do NOT verify absolute hole
locations, hole diameters, or dimensions in millimetres. The pattern tolerance
is empirical, not a manufacturing acceptance criterion.
"""

from __future__ import annotations

import csv
import json
import math
from itertools import combinations
from pathlib import Path

import cv2


ROOT = Path(__file__).resolve().parent
PHOTOS = ROOT / "photos"
OUTPUT = ROOT / "results_v5"
REFERENCE_FILE = ROOT / "hole_reference.json"

# Empirically chosen for the current black-PLA/white-paper setup.
FIXTURE_DARK_THRESHOLD = 90
HOLE_BRIGHT_THRESHOLD = 100
MIN_HOLE_AREA_PX2 = 100
EXPECTED_HOLE_COUNT = 3
# Permitted difference in each normalized triangle-side ratio, in percentage
# points. Empirical prototype setting, NOT a manufacturing tolerance.
PATTERN_RATIO_LIMIT_PP = 3.0
# Representative portrait ROI size from the original calibration photographs.
# Only its width:height ratio matters for calculating reference triangle shape.
REFERENCE_ROI_WIDTH = 196
REFERENCE_ROI_HEIGHT = 310
PREVIEW_WIDTH = 1142

# Independent trials are needed before making reliability claims.

TRIALS = [
    ("nominal_real.jpg", "Nominal", "Trial 1"),
    ("nominal_trial2.jpg", "Nominal", "Trial 2"),
    ("defective_real.jpg", "Defective", "Trial 1"),
    ("defective_trial2.jpg", "Defective", "Trial 2"),
    ("nominal_shift1.jpg", "Nominal", "Shift 1"),
    ("defective_shift1.jpg", "Defective", "Shift 1"),
    ("defective_bright1.jpg", "Defective", "Bright 1"),
    ("nominal_validation1.jpg", "Nominal", "Validation 1"),
    ("defective_validation1.jpg", "Defective", "Validation 1"),
    ("nominal_finaltest1.jpg", "Nominal", "Handheld Test 1"),
    ("defective_finaltest1.jpg", "Defective", "Handheld Test 1"),
]

def load_reference() -> list[tuple[float, float]]:
    """Load three nominal image positions (X%, Y%) in top-to-bottom order."""
    with REFERENCE_FILE.open(encoding="utf-8") as file:
        values = json.load(file)
    return [tuple(map(float, values[f"hole_{i}"])) for i in range(1, 4)]


def crop_fixture(photo):
    """Find the largest dark region and crop the calibrated internal ROI."""
    height = round(photo.shape[0] * PREVIEW_WIDTH / photo.shape[1])
    small = cv2.resize(photo, (PREVIEW_WIDTH, height))
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    _, dark_mask = cv2.threshold(
        gray, FIXTURE_DARK_THRESHOLD, 255, cv2.THRESH_BINARY_INV
    )
    contours, _ = cv2.findContours(
        dark_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )
    if not contours:
        raise ValueError("No dark regions found; fixture cannot be located")

    largest = max(contours, key=cv2.contourArea)
    x, y, width, height = cv2.boundingRect(largest)
    if width < 100 or height < 100:
        raise ValueError("Detected dark region is too small to be a fixture")

    x0 = x + int(0.22 * width)
    x1 = x + int(0.75 * width)
    y0 = y + int(0.18 * height)
    y1 = y + int(0.82 * height)
    cropped = small[y0:y1, x0:x1]
    if cropped.size == 0:
        raise ValueError("Automatic crop is empty")
    return cropped


def find_holes(cropped):
    """Find bright hole openings and their normalized centres."""
    gray = cv2.cvtColor(cropped, cv2.COLOR_BGR2GRAY)
    _, binary = cv2.threshold(
        gray, HOLE_BRIGHT_THRESHOLD, 255, cv2.THRESH_BINARY
    )
    contours, _ = cv2.findContours(
        binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )
    holes = []
    for contour in contours:
        if cv2.contourArea(contour) <= MIN_HOLE_AREA_PX2:
            continue
        moments = cv2.moments(contour)
        if moments["m00"] == 0:
            continue
        x = moments["m10"] / moments["m00"]
        y = moments["m01"] / moments["m00"]
        holes.append({
            "contour": contour,
            "center_px": (x, y),
            "center_percent": (100 * x / gray.shape[1], 100 * y / gray.shape[0]),
        })
    holes.sort(key=lambda item: item["center_percent"][1])
    return holes


def triangle_side_ratios(centres):
    """Sorted relative side lengths of a triangle in *pixel* coordinates.

    Ratios are independent of camera rotation, translation and uniform scale.
    They cannot check overall size, orientation, or the plate's fixture position.
    """
    side_lengths = sorted(math.dist(a, b) for a, b in combinations(centres, 2))
    if len(side_lengths) != 3 or side_lengths[-1] == 0:
        raise ValueError("Cannot calculate a three-hole triangle pattern")
    return [length / side_lengths[-1] for length in side_lengths]


def inspect(cropped, reference):
    """Check three detected holes against the *relative* reference pattern."""
    holes = find_holes(cropped)
    count_ok = len(holes) == EXPECTED_HOLE_COUNT
    errors = []
    if count_ok:
        # The reference JSON stores X% and Y% of a *portrait* calibration ROI.
        # Recreate its shape in pixels before computing rotation-invariant ratios.
        ref_centres = [
            (x_percent * REFERENCE_ROI_WIDTH / 100,
             y_percent * REFERENCE_ROI_HEIGHT / 100)
            for x_percent, y_percent in reference
        ]
        expected = triangle_side_ratios(ref_centres)
        observed = triangle_side_ratios([h["center_px"] for h in holes])
        errors = [100 * abs(got - wanted)
                  for got, wanted in zip(observed, expected)]

    pattern_ok = count_ok and all(
        error <= PATTERN_RATIO_LIMIT_PP for error in errors
    )
    result = "PASS" if count_ok and pattern_ok else "FAIL"
    return holes, count_ok, pattern_ok, errors, result


def save_visual(cropped, holes, reference, pattern_ok, result, destination):
    """Save a readable annotated graphic for demonstration and auditing."""
    scale = 2
    image = cv2.resize(cropped, None, fx=scale, fy=scale,
                       interpolation=cv2.INTER_NEAREST)
    color = (20, 150, 20) if result == "PASS" else (25, 25, 220)
    for hole in holes:
        contour = (hole["contour"] * scale).astype("int32")
        cv2.drawContours(image, [contour], -1, (0, 230, 0), 2)
        cx, cy = hole["center_px"]
        cv2.drawMarker(image, (round(cx * scale), round(cy * scale)),
                       (0, 0, 255), cv2.MARKER_CROSS, 15, 2)

    # Reference markers use a portrait crop and should never be interpreted as
    # expected absolute coordinates in a rotated/landscape image. Hide them there.
    h, w = cropped.shape[:2]
    if h > w:
        for x_percent, y_percent in reference:
            cx = round(x_percent / 100 * w * scale)
            cy = round(y_percent / 100 * h * scale)
            cv2.circle(image, (cx, cy), 9, (0, 180, 255), 2)

    header_h = 64
    canvas = cv2.copyMakeBorder(image, header_h, 0, 0, 0,
                                cv2.BORDER_CONSTANT, value=(35, 35, 35))
    label = f"{result}  |  {len(holes)}/{EXPECTED_HOLE_COUNT} holes"
    cv2.putText(canvas, label, (12, 42), cv2.FONT_HERSHEY_SIMPLEX,
                0.85, color, 2, cv2.LINE_AA)
    if not cv2.imwrite(str(destination), canvas):
        raise OSError(f"Could not write annotated image: {destination}")


def run() -> None:
    OUTPUT.mkdir(exist_ok=True)
    reference = load_reference()
    records = []
    correct = 0
    errors = 0

    print("\n--- REAL-PART INSPECTION ---")
    for name, actual_part, trial in TRIALS:
        source = PHOTOS / name
        row = {
            "Image": name, "Part": actual_part, "Condition": trial,
            "Holes Detected": "", "Hole Count Check": "",
            "Hole Pattern Check": "", "Max Pattern Error (pp)": "",
            "Inspection Result": "ERROR", "Expected Result": (
                "PASS" if actual_part == "Nominal" else "FAIL"
            ), "Correct Classification": "",
        }
        try:
            photo = cv2.imread(str(source))
            if photo is None:
                raise FileNotFoundError(f"Original photograph not found: {source}")
            crop = crop_fixture(photo)
            holes, count_ok, pattern_ok, pattern_errors, result = inspect(
                crop, reference
            )
            row["Holes Detected"] = len(holes)
            row["Hole Count Check"] = "PASS" if count_ok else "FAIL"
            row["Hole Pattern Check"] = (
                "PASS" if pattern_ok else "FAIL"
            ) if count_ok else "NOT EVALUATED"
            row["Max Pattern Error (pp)"] = (
                round(max(pattern_errors), 3) if pattern_errors else ""
            )
            row["Inspection Result"] = result
            row["Correct Classification"] = (
                "YES" if result == row["Expected Result"] else "NO"
            )
            output_path = OUTPUT / (source.stem + "_annotated.png")
            save_visual(crop, holes, reference, pattern_ok, result, output_path)
            correct += result == row["Expected Result"]
            print(f"{name}: {len(holes)} holes -> {result} [{trial}]")
        except (OSError, ValueError, cv2.error) as exc:
            errors += 1
            print(f"ERROR inspecting {name}: {exc}")
        records.append(row)

    output_csv = OUTPUT / "inspection_results.csv"
    with output_csv.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=records[0].keys())
        writer.writeheader()
        writer.writerows(records)

    checked = len(records) - errors
    print(f"\nEvaluated: {checked}/{len(records)} photos")
    if checked:
        print(f"Correct classifications: {correct}/{checked} ({correct / checked:.1%})")
    if errors:
        print(f"Warning: {errors} photo(s) were not evaluated; see errors above.")
    print("Note: These photographs include development and tuning examples; results are not independent reliability estimates.")
    print(f"CSV: {output_csv}")
    print(f"Annotated images: {OUTPUT}")


if __name__ == "__main__":
    run()
