"""Restore receipt rows from OCR quadrilaterals without changing their text."""
from __future__ import annotations

from statistics import median


def spatial_rows(boxes, texts, scores):
    regions = []
    slopes = []
    for box, text, score in zip(boxes, texts, scores):
        if not text.strip():
            continue
        width = max(point[0] for point in box) - min(point[0] for point in box)
        height = ((box[3][1] - box[0][1]) + (box[2][1] - box[1][1])) / 2
        slope = (box[1][1] - box[0][1]) / max(1, box[1][0] - box[0][0])
        if width > max(1, height) * 3 and abs(slope) < .25:
            slopes.append(slope)
        regions.append({'text': text.strip(), 'confidence': round(float(score) * 100, 2),
                        'x': sum(point[0] for point in box) / 4,
                        'y': sum(point[1] for point in box) / 4,
                        'height': max(1, height), 'box': [[float(x), float(y)] for x, y in box]})
    skew = median(slopes) if slopes else 0
    for region in regions:
        region['row_y'] = region['y'] - skew * region['x']
    rows = []
    for region in sorted(regions, key=lambda item: item['row_y']):
        if rows and abs(region['row_y'] - median(part['row_y'] for part in rows[-1])) < .65 * min(region['height'], median(part['height'] for part in rows[-1])):
            rows[-1].append(region)
        else:
            rows.append([region])
    return [{'text': ' '.join(part['text'] for part in sorted(row, key=lambda part: part['x'])),
             'confidence': min(part['confidence'] for part in row),
             'regions': sorted(row, key=lambda part: part['x'])} for row in rows]


def layout_text(boxes, texts, scores):
    rows = spatial_rows(boxes, texts, scores)
    return '\n'.join(row['text'] for row in rows), rows
