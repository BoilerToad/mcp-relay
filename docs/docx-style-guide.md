# Word Document Style Guide

For use when generating .docx files with docx-js for this project.

## General Principles

- No color schemes, callout boxes, or decorative elements
- Black text throughout; no colored headings
- Arial font, 11pt body (22 half-points in docx-js)
- US Letter, 1-inch margins
- Simple gray header/footer dividers only

## Fonts and Sizes

| Element | Font | Size (half-points) |
|---------|------|--------------------|
| Body | Arial | 22 |
| H1 | Arial bold | 28 |
| H2 | Arial bold | 24 |
| H3 | Arial bold | 22 |
| Small text (headers/footers) | Arial | 18 |
| Code/URLs inline | Courier New | 20 |

## Spacing

- H1: 360 before, 120 after
- H2: 240 before, 80 after
- H3: 200 before, 60 after
- Body paragraphs: 0 before, 120 after
- Bullet items: 0 before, 60 after
- Use `sp(n)` empty paragraphs for section breaks (120–360 typical)

## Headings

Plain bold, no color, no border underlining.

```javascript
function h1(text) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_1,
    children: [new TextRun({ text, bold: true, size: 28, font: "Arial" })],
    spacing: { before: 360, after: 120 }
  });
}

function h2(text) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_2,
    children: [new TextRun({ text, bold: true, size: 24, font: "Arial" })],
    spacing: { before: 240, after: 80 }
  });
}

function h3(text) {
  return new Paragraph({
    children: [new TextRun({ text, bold: true, size: 22, font: "Arial" })],
    spacing: { before: 200, after: 60 }
  });
}
```

## Body and Inline Helpers

```javascript
function body(text) {
  return new Paragraph({
    children: [new TextRun({ text, size: 22, font: "Arial" })],
    spacing: { before: 0, after: 120 }
  });
}

function r(text)  { return new TextRun({ text, size: 22, font: "Arial" }); }
function rb(text) { return new TextRun({ text, bold: true, size: 22, font: "Arial" }); }
function ri(text) { return new TextRun({ text, italics: true, size: 22, font: "Arial" }); }
function rc(text) { return new TextRun({ text, size: 20, font: "Courier New" }); }
```

## Bullets

Use `LevelFormat.BULLET` with numbering config. Never unicode bullet characters directly.

```javascript
numbering: {
  config: [{
    reference: "bullets",
    levels: [
      { level: 0, format: LevelFormat.BULLET, text: "\u2022", alignment: AlignmentType.LEFT,
        style: { paragraph: { indent: { left: 720, hanging: 360 } } } },
      { level: 1, format: LevelFormat.BULLET, text: "\u25E6", alignment: AlignmentType.LEFT,
        style: { paragraph: { indent: { left: 1080, hanging: 360 } } } }
    ]
  }]
}

function bullet(runs, level = 0) {
  const children = typeof runs === 'string'
    ? [new TextRun({ text: runs, size: 22, font: "Arial" })]
    : runs;
  return new Paragraph({
    numbering: { reference: "bullets", level },
    children,
    spacing: { before: 0, after: 60 }
  });
}
```

## Tables

Gray header row (`EEEEEE`), white data rows, light gray borders (`BBBBBB`).
Always use `WidthType.DXA`. Always set `columnWidths` AND per-cell `width`.

```javascript
const border = { style: BorderStyle.SINGLE, size: 1, color: "BBBBBB" };
const borders = { top: border, bottom: border, left: border, right: border };

function makeTable(headers, rows, colWidths) {
  const headerRow = new TableRow({
    tableHeader: true,
    children: headers.map((h, i) => new TableCell({
      borders,
      width: { size: colWidths[i], type: WidthType.DXA },
      shading: { fill: "EEEEEE", type: ShadingType.CLEAR },
      margins: { top: 80, bottom: 80, left: 100, right: 100 },
      children: [new Paragraph({ children: [new TextRun({ text: h, bold: true, size: 20, font: "Arial" })] })]
    }))
  });
  const dataRows = rows.map(row => new TableRow({
    children: row.map((cell, i) => new TableCell({
      borders,
      width: { size: colWidths[i], type: WidthType.DXA },
      shading: { fill: "FFFFFF", type: ShadingType.CLEAR },
      margins: { top: 80, bottom: 80, left: 100, right: 100 },
      children: [new Paragraph({ children: [new TextRun({ text: cell, size: 20, font: "Arial" })] })]
    }))
  }));
  const total = colWidths.reduce((a, b) => a + b, 0);
  return new Table({ width: { size: total, type: WidthType.DXA }, columnWidths: colWidths, rows: [headerRow, ...dataRows] });
}
```

## Shaded Text Block (single use case: quoted/draft paragraphs)

Light gray background (`F5F5F5`), same gray border. Use sparingly — only for set-aside blocks like draft text.

```javascript
new Table({
  width: { size: 9360, type: WidthType.DXA },
  columnWidths: [9360],
  rows: [new TableRow({
    children: [new TableCell({
      borders,
      width: { size: 9360, type: WidthType.DXA },
      shading: { fill: "F5F5F5", type: ShadingType.CLEAR },
      margins: { top: 160, bottom: 160, left: 200, right: 200 },
      children: [new Paragraph({
        children: [new TextRun({ text: "...", size: 22, font: "Arial" })]
      })]
    })]
  })]
})
```

## Header and Footer

Thin gray divider line. Simple text only — no color, no logos.

```javascript
headers: {
  default: new Header({
    children: [new Paragraph({
      border: { bottom: { style: BorderStyle.SINGLE, size: 4, color: "888888", space: 4 } },
      children: [
        new TextRun({ text: "Document title", size: 18, font: "Arial", color: "555555" }),
        new TextRun({ text: "\tDate", size: 18, font: "Arial", color: "555555" })
      ],
      tabStops: [{ type: TabStopType.RIGHT, position: TabStopPosition.MAX }]
    })]
  })
},
footers: {
  default: new Footer({
    children: [new Paragraph({
      border: { top: { style: BorderStyle.SINGLE, size: 4, color: "888888", space: 4 } },
      children: [
        new TextRun({ text: "Author  |  context", size: 18, font: "Arial", color: "555555" }),
        new TextRun({ text: "\tFor peer review", size: 18, font: "Arial", color: "555555" })
      ],
      tabStops: [{ type: TabStopType.RIGHT, position: TabStopPosition.MAX }]
    })]
  })
}
```

## Page Setup

```javascript
properties: {
  page: {
    size: { width: 12240, height: 15840 },  // US Letter
    margin: { top: 1440, right: 1440, bottom: 1440, left: 1440 }  // 1-inch margins
  }
}
```

## What to Avoid

- Colored headings or text
- Callout boxes with colored backgrounds
- Decorative borders on headings
- Tables used as layout elements (dividers, sidebars)
- Unicode bullet characters inserted directly
- `WidthType.PERCENTAGE` in tables (breaks in Google Docs)
- `\n` inside TextRun — use separate Paragraph elements
