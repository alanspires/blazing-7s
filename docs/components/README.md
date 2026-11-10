# Component references

## CMD G65SC22

`g65sc22-cmd.pdf`: California Micro Devices G65SC22 manufacturer datasheet,
dated February 28, 2001, 20 PDF pages. Archived from:

https://www.waveguide.se/?article=41&file=g65sc22.pdf

SHA-256: `0b3d5c496ddab9491587989eb50ac29e96b5f3a7232387a8e51797b70aa4aff5`.

Relevant headings: Signal Description, Timer 1 Operation, Timer 2 Operation,
Timer 2 One-Shot Mode, Interrupt Operation; Figures 24, 25, 27, 37 and 38.
The timer/interrupt descriptions were visually checked in rendered pages; most
body text is scanned and is absent from ordinary PDF text extraction.

U41 is marked **CMD G65SC22PI-2**, with `0304` below it (observed on 2026-10-17, from
photographs that are not distributed; both VIAs in `../../photos/05-mpu-eproms.jpg`
carry this marking). This identifies a matching manufacturer reference, replacing
reliance on a compatible WDC-family description. Neither the marking nor the
datasheet qualifies actual clock/bus timing, silicon condition or
board behavior; see `../../firmware/IRQ-AUDIT.md`.

## ST M27C4001

`m27c4001-st.pdf`: STMicroelectronics M27C4001 datasheet, Rev 4, March 2006,
24 PDF pages. Downloaded from the distributor-hosted manufacturer document:

https://media.digikey.com/pdf/Data%20Sheets/ST%20Microelectronics%20PDFS/M27C4001.pdf

SHA-256: `ca938cfdba8d82cb798639a630a4d99c28dc933ffae3c4e1dff37ffdbd0de813`.

Relevant headings: Summary description, DIP Connections, Programming, PRESTO II
Programming Algorithm, Electronic Signature, DC and AC parameters, Part numbering.
The device is obsolete. The document does not establish the identity, authenticity,
speed or condition of any physical chip in this project.

See `../../firmware/PROGRAMMING.md` for the T48 catalogue evidence, image layout,
future spare-chip verification procedure and unresolved physical checks.
